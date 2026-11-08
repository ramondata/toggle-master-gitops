"""Inspeciona o resultado Helm, incluindo o TLS e as recargas dos sidecars."""
from pathlib import Path
import sys
import yaml

docs = [obj for obj in yaml.safe_load_all(Path(sys.argv[1]).read_text()) if isinstance(obj, dict)]
one = lambda kind, name: next(obj for obj in docs if obj.get("kind") == kind and obj["metadata"]["name"] == name)
service = one("Service", "grafana")
assert service["spec"]["type"] == "LoadBalancer"
assert service["spec"]["ports"][0]["port"] == 443
assert not any(obj.get("kind") == "Ingress" for obj in docs)
pod = one("Deployment", "grafana")["spec"]["template"]["spec"]
grafana = next(c for c in pod["containers"] if c["name"] == "grafana")
env = {e["name"]: e for e in grafana["env"]}
assert env["GF_SERVER_PROTOCOL"]["value"] == "https"
for probe in ("livenessProbe", "readinessProbe"):
    assert grafana[probe]["httpGet"]["scheme"] == "HTTPS"
assert any(v.get("secret", {}).get("secretName") == "grafana-tls" for v in pod["volumes"])
for container in pod["containers"]:
    if container["name"] == "grafana":
        continue
    env = {e["name"]: e for e in container.get("env", [])}
    if "REQ_URL" in env:
        assert env["REQ_URL"]["value"].startswith("https://localhost:3000/")
        assert env["SKIP_TLS_VERIFY"]["value"] == "true"
assert one("Service", "monitoring-prometheus")["spec"]["type"] == "ClusterIP"
print("Render Academy validado: Grafana HTTPS, probes HTTPS, recargas HTTPS, Prometheus privado.")
