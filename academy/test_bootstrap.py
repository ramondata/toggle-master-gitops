"""Contratos que podem quebrar uma retomada real do lab sem acesso a AWS."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apps
import runtime
import capacity


class BootstrapTests(unittest.TestCase):
    def test_capacity_reserves_slots_and_only_moves_our_deployments(self):
        condition = {"type": "Ready", "status": "True"}
        nodes = [{"metadata": {"name": name}, "status": {"allocatable": {"pods": str(size)}, "conditions": [condition]}}
                 for name, size in (("full", 3), ("free", 6), ("reserved", 3))]
        def pod(name, node, ns="kube-system", labels=None, owner=None):
            return {"metadata": {"name": name, "namespace": ns, "labels": labels or {}, "ownerReferences": owner or []},
                    "spec": {"nodeName": node}, "status": {"phase": "Running", "conditions": [condition]}}
        app = pod("auth", "full", "toggle-master", owner=[{"kind": "ReplicaSet", "name": "auth-rs"}])
        pods = [app, pod("exporter", "full", "observability", {"app.kubernetes.io/name": "prometheus-node-exporter"}),
                pod("system", "full"), pod("system", "free"), pod("system", "reserved")]
        rs = [{"metadata": {"name": "auth-rs", "namespace": "toggle-master", "ownerReferences": [{"kind": "Deployment", "name": "auth-service"}]}}]
        move = capacity.next_move(nodes, pods, rs)
        self.assertEqual(move["deployment"], "auth-service")
        self.assertEqual(set(move["hold"]), {"full", "reserved"})
        app["spec"]["nodeName"] = "free"
        self.assertIsNone(capacity.next_move(nodes, pods, rs))
        app["spec"]["nodeName"] = "full"
        rs[0]["metadata"]["ownerReferences"][0]["name"] = "unrelated-project"
        with self.assertRaises(RuntimeError):
            capacity.next_move(nodes, pods, rs)

    def test_capacity_preserves_existing_cordon(self):
        node = {"metadata": {"name": "maintenance"}, "spec": {"unschedulable": True},
                "status": {"allocatable": {"pods": "1"}, "conditions": [{"type": "Ready", "status": "True"}]}}
        self.assertIsNone(capacity.next_move([node], [], []))

    def test_expired_or_incomplete_session_never_reaches_secret(self):
        now = datetime.now(timezone.utc)
        valid = {"AccessKeyId": "test-key", "SecretAccessKey": "test-secret", "SessionToken": "test-token",
                 "Expiration": (now + timedelta(hours=2)).isoformat()}
        self.assertEqual(runtime.session_values(valid, now)["AWS_SESSION_TOKEN"], "test-token")
        for change in ({"Expiration": (now - timedelta(seconds=1)).isoformat()}, {"SessionToken": ""}):
            with self.assertRaises(runtime.ConfigurationError):
                runtime.session_values(dict(valid, **change), now)

    def test_urls_cannot_use_local_emulator(self):
        for url in ("postgresql://u:p@localhost/auth_db", "postgresql://u:p@floci:4566/auth_db",
                    "http://u:p@db.rds.amazonaws.com/auth_db"):
            with self.assertRaises(runtime.ConfigurationError):
                runtime.validate_db_url(url)

    def test_rds_password_encoding_and_original_two_instance_layout(self):
        dbs = [{"DBName": name + "_db", "DBInstanceStatus": "available", "MasterUsername": "admin",
                "Endpoint": {"Address": name + ".example.rds.amazonaws.com", "Port": 5432}}
               for name in ("auth", "target")]
        actual = runtime.database_values({}, dbs, password="a@:/?#b")
        self.assertIn("a%40%3A%2F%3F%23b", actual["auth-url"])
        self.assertTrue(actual["flag-url"].endswith("/flags_db?sslmode=require"))
        self.assertIn("target.example.rds.amazonaws.com", actual["targeting-url"])
        self.assertEqual(actual, runtime.database_values(actual, []))

    def test_duplicate_rds_is_not_guessed(self):
        db = {"DBName": "auth_db", "DBInstanceStatus": "available"}
        with self.assertRaises(runtime.ConfigurationError):
            runtime.database_values({}, [db, db], password="test-only")

    def test_secret_replace_uses_resource_version_and_stdin(self):
        old = {"metadata": {"name": "academy-runtime", "namespace": "toggle-master", "resourceVersion": "123"},
               "data": {"AWS_SESSION_TOKEN": base64.b64encode(b"old").decode()}}
        with patch.object(runtime, "capture", return_value=json.dumps(old)), patch.object(runtime.subprocess, "run") as run:
            run.return_value.returncode = 0
            with patch("sys.stdout", new=io.StringIO()) as output:
                runtime.put_secret("toggle-master", "academy-runtime", {"AWS_SESSION_TOKEN": "private-test-token"})
            args, kwargs = run.call_args
            self.assertEqual(args[0], ["kubectl", "replace", "-f", "-"])
            payload = json.loads(kwargs["input"])
            self.assertEqual(payload["metadata"]["resourceVersion"], "123")
            self.assertNotIn("private-test-token", output.getvalue())
            self.assertNotIn("private-test-token", str(args))

    def test_refresh_preserves_connections_and_drops_stale_credentials(self):
        current = {"AWS_SQS_URL": "https://sqs.us-east-1.amazonaws.com/111122223333/project",
                   "AWS_DYNAMODB_TABLE": "user-events", "REDIS_URL": "redis://redis.example.cache.amazonaws.com:6379",
                   "AWS_SESSION_TOKEN": "expired-token"}
        def fake_aws(*args):
            if args[:2] == ("configure", "export-credentials"):
                return {"AccessKeyId": "new-key", "SecretAccessKey": "new-secret", "SessionToken": "new-token"}
            if args[:2] == ("sts", "get-caller-identity"):
                return {"Account": "111122223333"}
            if args[:2] == ("dynamodb", "describe-table"):
                return {"Table": {"KeySchema": [{"AttributeName": "event_id", "KeyType": "HASH"}]}}
            raise AssertionError(args)
        with patch.dict(runtime.os.environ, {}, clear=True), patch.object(runtime, "get_secret", return_value=current), \
             patch.object(runtime, "aws", side_effect=fake_aws), patch.object(runtime, "put_secret") as put:
            runtime.configure_runtime()
            actual = put.call_args.args[2]
            self.assertEqual(actual["AWS_SESSION_TOKEN"], "new-token")
            for key in ("AWS_SQS_URL", "AWS_DYNAMODB_TABLE", "REDIS_URL"):
                self.assertEqual(actual[key], current[key])
            self.assertNotIn("AWS_ENDPOINT_URL", actual)

    def test_applicationset_keeps_hpa_and_restart_exceptions(self):
        obj = apps.services("master", "111122223333", "us-east-1", "cfg", "aws")
        template = obj["spec"]["template"]["spec"]
        self.assertEqual(len(obj["spec"]["generators"][0]["list"]["elements"]), 6)
        self.assertTrue(any("restartedAt" in pointer for entry in template["ignoreDifferences"] for pointer in entry["jsonPointers"]))
        self.assertIn("RespectIgnoreDifferences=true", template["syncPolicy"]["syncOptions"])
        self.assertNotIn("local/", json.dumps(obj))

    def test_wait_rejects_healthy_from_previous_config(self):
        source = {"targetRevision": "master", "helm": {"valuesObject": {"clusterName": "toggle-cluster"}}}
        app = {"spec": {"source": source}, "status": {"health": {"status": "Healthy"},
               "sync": {"status": "Synced", "comparedTo": {"source": copy.deepcopy(source)}},
               "operationState": {"phase": "Succeeded", "syncResult": {"source": copy.deepcopy(source)}}}}
        self.assertTrue(apps.ready(app))
        self.assertFalse(apps.ready(app, aws_revision="new-session"))
        stale = copy.deepcopy(app)
        stale["spec"]["source"]["helm"]["valuesObject"]["clusterName"] = "new-cluster"
        self.assertFalse(apps.ready(stale))

    def test_database_job_has_bounded_deadline_and_secret_refs(self):
        with patch("sys.stdout", new=io.StringIO()) as output:
            runtime.init_database_job("111122223333.dkr.ecr.us-east-1.amazonaws.com/toggle-master/flag-service:test")
        manifest = json.loads(output.getvalue())
        job = manifest["items"][1]
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertEqual(job["spec"]["activeDeadlineSeconds"], 180)
        env = job["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertTrue(all(item["valueFrom"]["secretKeyRef"]["name"] == "academy-databases" for item in env))


if __name__ == "__main__":
    unittest.main()
