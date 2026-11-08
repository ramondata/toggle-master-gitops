#!/usr/bin/env python3
"""Configura Secrets via stdin do kubectl, sem gravar credenciais no checkout."""
import base64
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from urllib.parse import quote, urlsplit

REGION = os.getenv("TARGET_REGION", "us-east-1")
CLUSTER = os.getenv("TARGET_CLUSTER", "toggle-cluster")


class ConfigurationError(Exception):
    pass


def capture(command):
    result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=90)
    if result.returncode:
        # Nao reproduzir stdout/stderr de consultas que possam conter Secrets.
        raise ConfigurationError("Falha em " + " ".join(command[:3]) + "; confira sessao, recurso e permissoes.")
    return result.stdout


def aws(*args):
    return json.loads(capture(["aws", *args, "--region", REGION, "--output", "json", "--no-cli-pager"]))


def get_secret(namespace, name):
    raw = capture(["kubectl", "-n", namespace, "get", "secret", name, "--ignore-not-found", "-o", "json"])
    if not raw.strip():
        return {}
    data = json.loads(raw).get("data", {})
    return {key: base64.b64decode(value, validate=True).decode() for key, value in data.items()}


def put_secret(namespace, name, data):
    obj = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
           "metadata": {"namespace": namespace, "name": name},
           "data": {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}}
    raw = capture(["kubectl", "-n", namespace, "get", "secret", name, "--ignore-not-found", "-o", "json"])
    operation = "create"
    if raw.strip():
        old = json.loads(raw)
        if old.get("data", {}) == obj["data"]:
            print("Secret preservado: " + namespace + "/" + name)
            return
        # resourceVersion impede sobrescrever uma renovacao concorrente.
        obj["metadata"] = {k: v for k, v in old["metadata"].items() if k != "managedFields"}
        operation = "replace"
    result = subprocess.run(["kubectl", operation, "-f", "-"],
                            input=json.dumps(obj), text=True, capture_output=True, check=False, timeout=45)
    if result.returncode:
        raise ConfigurationError("Nao foi possivel atualizar o Secret " + name + ". Nenhuma credencial foi impressa.")
    print("Secret atualizado: " + namespace + "/" + name)


def session_values(data, now=None):
    fields = {"AWS_ACCESS_KEY_ID": "AccessKeyId", "AWS_SECRET_ACCESS_KEY": "SecretAccessKey", "AWS_SESSION_TOKEN": "SessionToken"}
    if any(not data.get(key) or any(c.isspace() for c in data[key]) for key in fields.values()):
        raise ConfigurationError("Sessao temporaria incompleta. Inicie/renove o lab Academy.")
    expiry = data.get("Expiration")
    if expiry:
        try:
            remaining = (datetime.fromisoformat(expiry.replace("Z", "+00:00")) - (now or datetime.now(timezone.utc))).total_seconds()
        except (ValueError, TypeError):
            raise ConfigurationError("Expiration invalida na sessao AWS.") from None
        if remaining < 1200:
            raise ConfigurationError("Renove a sessao Academy: faltam menos de 20 minutos para expirar.")
    return {dest: data[source] for dest, source in fields.items()}


def require_aws_host(host):
    if not host or not re.fullmatch(r"[a-zA-Z0-9.-]+\.amazonaws\.com", host):
        raise ConfigurationError("Esperado endpoint AWS real terminado em .amazonaws.com.")


def validate_db_url(value):
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("postgres", "postgresql") or not parts.username or not parts.password or not parts.path.strip("/"):
            raise ConfigurationError("URL PostgreSQL incompleta.")
        require_aws_host(parts.hostname)
    except ValueError:
        raise ConfigurationError("URL PostgreSQL invalida; use URL encoding na senha.") from None
    return value


def database_values(existing, instances, password=None):
    """Descobre os tres RDS do Terraform na VPC do EKS. Preserva chaves ja usadas."""
    output = dict(existing)
    missing = [name for name in ("auth", "flag", "targeting") if not output.get(name + "-url")]
    if missing:
        by_name = {}
        for instance in instances:
            name = instance.get("DBName", "").removesuffix("_db")
            if name == "target":
                name = "targeting"
            elif name == "flags":
                name = "flag"
            if name in missing and instance.get("DBInstanceStatus") == "available":
                if name in by_name:
                    raise ConfigurationError("Mais de um RDS para " + name + "; preencha as URLs com academy/lab.sh databases.")
                by_name[name] = instance
        # O Terraform original cria RDS auth/target. flags_db pode compartilhar
        # o RDS auth: o Job cria o database ausente antes das tabelas.
        if "flag" in missing and "flag" not in by_name and "auth" in by_name:
            by_name["flag"] = dict(by_name["auth"], DBName="flags_db")
        if all(name in by_name for name in missing):
            password = password if password is not None else getpass.getpass("Senha do usuario master dos RDS (entrada oculta): ")
            if not password:
                raise ConfigurationError("A senha RDS nao pode ser vazia.")
            for name in missing:
                instance = by_name[name]
                endpoint = instance["Endpoint"]
                require_aws_host(endpoint["Address"])
                output[name + "-url"] = ("postgresql://" + quote(instance["MasterUsername"], safe="") + ":"
                    + quote(password, safe="") + "@" + endpoint["Address"] + ":" + str(endpoint["Port"])
                    + "/" + instance["DBName"] + "?sslmode=require")
        else:
            for name in missing:
                output[name + "-url"] = getpass.getpass("DATABASE_URL do " + name + " (entrada oculta): ")
    for name in ("auth", "flag", "targeting"):
        validate_db_url(output[name + "-url"])
    output.setdefault("master-key", secrets.token_hex(32))
    output.setdefault("service-api-key", secrets.token_hex(32))
    if not output["master-key"] or not output["service-api-key"]:
        raise ConfigurationError("master-key/service-api-key vazia no Secret existente.")
    return output


def configure_runtime():
    current = get_secret("toggle-master", "academy-runtime")
    session = session_values(aws("configure", "export-credentials", "--format", "process"))
    identity = aws("sts", "get-caller-identity")
    queue = os.getenv("AWS_SQS_URL") or current.get("AWS_SQS_URL")
    if not queue:
        name = os.getenv("SQS_QUEUE_NAME")
        if name:
            queue = aws("sqs", "get-queue-url", "--queue-name", name)["QueueUrl"]
        else:
            urls = aws("sqs", "list-queues").get("QueueUrls", [])
            matches = [url for url in urls if url.rsplit("/", 1)[-1] in ("toggle-master-sqs", "feature-evaluation-events")]
            if len(matches) != 1:
                raise ConfigurationError("Defina SQS_QUEUE_NAME ou AWS_SQS_URL: nenhuma fila unica do projeto foi encontrada.")
            queue = matches[0]
    url = urlsplit(queue)
    require_aws_host(url.hostname)
    if url.scheme != "https" or not url.path.startswith("/" + identity["Account"] + "/"):
        raise ConfigurationError("A fila SQS precisa ser HTTPS e pertencer a conta AWS atual. Defina AWS_SQS_URL se a conta mudou.")
    table = os.getenv("AWS_DYNAMODB_TABLE") or current.get("AWS_DYNAMODB_TABLE")
    if not table:
        tables = aws("dynamodb", "list-tables")["TableNames"]
        matches = [name for name in tables if name in ("dev-dynamodb", "user-events")]
        if len(matches) != 1:
            raise ConfigurationError("Defina AWS_DYNAMODB_TABLE: nenhuma tabela unica do projeto foi encontrada.")
        table = matches[0]
    desc = aws("dynamodb", "describe-table", "--table-name", table)["Table"]
    if desc["KeySchema"] != [{"AttributeName": "event_id", "KeyType": "HASH"}]:
        raise ConfigurationError("DynamoDB precisa usar event_id como unica chave HASH.")
    redis = os.getenv("REDIS_URL") or current.get("REDIS_URL")
    if not redis:
        groups = aws("elasticache", "describe-replication-groups")["ReplicationGroups"]
        group_id = os.getenv("REDIS_REPLICATION_GROUP", "dev-redis")
        matches = [g for g in groups if g["ReplicationGroupId"] == group_id]
        if len(matches) != 1:
            raise ConfigurationError("Redis nao encontrado. Defina REDIS_REPLICATION_GROUP ou REDIS_URL.")
        endpoint = matches[0]["NodeGroups"][0]["PrimaryEndpoint"]
        redis = "redis://" + endpoint["Address"] + ":" + str(endpoint["Port"])
    # ElastiCache tambem usa .cache.amazonaws.com.
    require_aws_host(urlsplit(redis).hostname)
    if urlsplit(redis).scheme not in ("redis", "rediss"):
        raise ConfigurationError("REDIS_URL precisa usar redis:// ou rediss://.")
    # Substitui o conjunto completo: nunca carrega AWS_ENDPOINT_URL de um emulador.
    values = dict(session, AWS_REGION=REGION, AWS_DEFAULT_REGION=REGION,
                  AWS_EC2_METADATA_DISABLED="true", AWS_SQS_URL=queue,
                  AWS_DYNAMODB_TABLE=table, REDIS_URL=redis)
    if current.get("AWS_ENDPOINT_URL"):
        raise ConfigurationError("academy-runtime contem AWS_ENDPOINT_URL. Use um namespace EKS sem configuracao de emulador.")
    put_secret("toggle-master", "academy-runtime", values)


def configure_databases(force=False):
    existing = get_secret("toggle-master", "academy-databases")
    if force:
        for name in ("auth", "flag", "targeting"):
            existing[name + "-url"] = getpass.getpass("DATABASE_URL do " + name + " (entrada oculta): ")
    cluster = aws("eks", "describe-cluster", "--name", CLUSTER)["cluster"]
    vpc = cluster["resourcesVpcConfig"]["vpcId"]
    instances = []
    if any(not existing.get(name + "-url") for name in ("auth", "flag", "targeting")):
        instances = [db for db in aws("rds", "describe-db-instances")["DBInstances"]
                     if db.get("DBSubnetGroup", {}).get("VpcId") == vpc]
    values = database_values(existing, instances)
    if values != existing:
        put_secret("toggle-master", "academy-databases", values)
    else:
        print("Conexoes RDS e chaves existentes preservadas.")


def integrations():
    values = get_secret("observability", "fase4-integrations")
    rotate = os.getenv("ROTATE_INTEGRATIONS", "false") == "true"
    if rotate or any(not values.get(key) for key in ("new-relic-license-key", "pagerduty-routing-key", "discord-webhook-url")):
        values["new-relic-license-key"] = getpass.getpass("New Relic INGEST license key: ")
        values["pagerduty-routing-key"] = getpass.getpass("PagerDuty Events v2 routing key: ")
        values["discord-webhook-url"] = getpass.getpass("Discord webhook URL: ")
    if (len(values["new-relic-license-key"]) < 10
        or not re.fullmatch(r"[a-zA-Z0-9]{32}", values["pagerduty-routing-key"])
        or not re.fullmatch(r"https://discord\.com/api/webhooks/\d+/[a-zA-Z0-9._-]+", values["discord-webhook-url"])):
        raise ConfigurationError("Credenciais externas incompletas/invalidas. Nenhuma chave foi impressa.")
    values.setdefault("heal-token", secrets.token_hex(32))
    put_secret("observability", "fase4-integrations", values)
    if not get_secret("toggle-master", "fase4-demo"):
        put_secret("toggle-master", "fase4-demo", {"demo-token": secrets.token_hex(32)})
    if not get_secret("observability", "grafana-admin"):
        put_secret("observability", "grafana-admin", {"admin-user": "admin", "admin-password": secrets.token_hex(24)})


def init_database_job(image):
    root = Path(__file__).resolve().parent
    code = (root / "init_databases.py").read_text()
    data = {path.name: path.read_text() for path in (root / "sql").glob("*.sql")}
    data["init.py"] = code
    config = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "academy-db-init", "namespace": "toggle-master"}, "data": data}
    job = {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "academy-db-init", "namespace": "toggle-master"},
           "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 180, "ttlSecondsAfterFinished": 3600,
                    "template": {"spec": {"restartPolicy": "Never", "containers": [{
                        "name": "init", "image": image, "command": ["python", "/bootstrap/init.py"],
                        "env": [{"name": key, "valueFrom": {"secretKeyRef": {"name": "academy-databases", "key": value}}}
                                for key, value in (("AUTH_URL", "auth-url"), ("FLAG_URL", "flag-url"), ("TARGETING_URL", "targeting-url"), ("SERVICE_API_KEY", "service-api-key"))],
                        "resources": {"requests": {"cpu": "20m", "memory": "64Mi"}, "limits": {"cpu": "250m", "memory": "256Mi"}},
                        "volumeMounts": [{"name": "code", "mountPath": "/bootstrap", "readOnly": True}],
                    }], "volumes": [{"name": "code", "configMap": {"name": "academy-db-init"}}]}}}}
    print(json.dumps({"apiVersion": "v1", "kind": "List", "items": [config, job]}))


def main():
    mode = sys.argv[1]
    if mode == "runtime":
        configure_runtime()
    elif mode in ("databases", "configure"):
        configure_databases(force=mode == "databases")
    elif mode == "integrations":
        integrations()
    elif mode == "init-manifest":
        init_database_job(sys.argv[2])
    else:
        raise ConfigurationError("Modo invalido")


if __name__ == "__main__":
    try:
        main()
    except (ConfigurationError, EOFError, KeyboardInterrupt) as exc:
        print(str(exc) if isinstance(exc, ConfigurationError) else "Entrada cancelada.", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print("Configuracao interrompida: " + type(exc).__name__ + ". Nenhum valor privado foi impresso.", file=sys.stderr)
        sys.exit(2)
