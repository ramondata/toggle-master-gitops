"""Publicacao pessoal usa STS de uma hora; nunca exporta a sessao curta."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class CiCredentialTests(unittest.TestCase):
    def execute(self, account, deny=False):
        root_repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binaries = root / "bin"
            binaries.mkdir()
            aws = """#!/usr/bin/env python3
import datetime, json, os, sys
from pathlib import Path
args=sys.argv[1:]
with (Path(os.environ['MOCK_ROOT'])/'aws-calls').open('a') as stream:
    stream.write(json.dumps(args)+'\\n')
if args[:2]==['sts','get-caller-identity']:
    print(os.environ['MOCK_ACCOUNT'])
elif args[:2]==['sts','assume-role']:
    if os.environ['MOCK_DENY']=='true':
        raise SystemExit(1)
    expiry=datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=1)
    print(json.dumps(dict(AccessKeyId='fixture-access',SecretAccessKey='fixture-secret',SessionToken='fixture-token',Expiration=expiry.isoformat())))
elif args[:2]==['configure','export-credentials']:
    expiry=datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(minutes=9)
    print(json.dumps(dict(AccessKeyId='fixture-access',SecretAccessKey='fixture-secret',SessionToken='fixture-token',Expiration=expiry.isoformat())))
"""
            gh = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args=sys.argv[1:]
with (Path(os.environ['MOCK_ROOT'])/'gh-calls').open('a') as stream:
    stream.write(json.dumps(args)+'\\n')
if args==['api','user','--jq','.login']: print('ramondata')
elif args[:2]==['secret','set']: sys.stdin.read()
elif args[:2]==['run','list']: print('123')
elif args[:2]==['run','view']: print('completed' if '.status' in args else 'success')
"""
            for name, content in (("aws", aws), ("gh", gh)):
                target = binaries / name
                target.write_text(content)
                target.chmod(0o700)
            env = dict(os.environ, PATH=str(binaries)+os.pathsep+os.environ["PATH"],
                       MOCK_ROOT=directory, MOCK_ACCOUNT=account, MOCK_DENY=str(deny).lower(),
                       TARGET_REGION="us-east-2", WAIT_FOR_CI="false", FORCE_REPUBLISH="false")
            completed = subprocess.run(["bash", str(root_repo/"scripts/fase4-refresh-ci.sh")],
                                       env=env, text=True, capture_output=True, check=False, timeout=30)
            calls = [json.loads(line) for line in (root/"aws-calls").read_text().splitlines()]
            github = [json.loads(line) for line in (root/"gh-calls").read_text().splitlines()]
            self.assertNotIn("fixture-secret", completed.stdout+completed.stderr)
            return completed, calls, github

    def test_personal_assumes_scoped_role_for_one_hour(self):
        completed, calls, github = self.execute("172346499417")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        assume = [call for call in calls if call[:2] == ["sts", "assume-role"]]
        self.assertEqual(len(assume), 1)
        self.assertIn("3600", assume[0])
        self.assertIn("arn:aws:iam::172346499417:role/toggle-cluster-ci-publisher", assume[0])
        self.assertFalse(any(call[:2] == ["configure", "export-credentials"] for call in calls))
        self.assertEqual(len([call for call in github if call[:2] == ["secret", "set"]]), 5)

    def test_assume_role_failure_never_updates_github_secrets(self):
        completed, _, github = self.execute("172346499417", deny=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(any(call[:2] == ["secret", "set"] for call in github))

    def test_academy_keeps_short_session_guard(self):
        completed, calls, github = self.execute("111122223333")
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(any(call[:2] == ["sts", "assume-role"] for call in calls))
        self.assertFalse(any(call[:2] == ["secret", "set"] for call in github))


if __name__ == "__main__":
    unittest.main()
