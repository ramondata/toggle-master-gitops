"""Planeja uma redistribuicao sem excluir pods nem mexer em workloads alheios."""
import json
import sys
from pathlib import Path

OWNED = {
    "toggle-master": {"auth-service", "flag-service", "targeting-service", "evaluation-service", "analytics-service"},
    "argocd": {"argocd-server", "argocd-repo-server", "argocd-redis", "argocd-dex-server", "argocd-applicationset-controller", "argocd-notifications-controller"},
}


def ready(obj):
    return any(item.get("type") == "Ready" and item.get("status") == "True" for item in obj.get("status", {}).get("conditions", []))


def next_move(nodes, pods, replicasets):
    deployments = {}
    for rs in replicasets:
        owner = next((o for o in rs["metadata"].get("ownerReferences", []) if o["kind"] == "Deployment"), None)
        if owner:
            deployments[(rs["metadata"]["namespace"], rs["metadata"]["name"])] = owner["name"]
    active = [p for p in pods if p.get("status", {}).get("phase") not in ("Succeeded", "Failed")]
    slots = {}
    for node in nodes:
        if node.get("spec", {}).get("unschedulable") or not ready(node):
            continue
        name = node["metadata"]["name"]
        assigned = [p for p in active if p.get("spec", {}).get("nodeName") == name]
        labels = [p["metadata"].get("labels", {}) for p in assigned]
        needed = int(not any(l.get("app.kubernetes.io/name") == "prometheus-node-exporter" for l in labels))
        needed += int(not any(l.get("app") == "otel-logs" for l in labels))
        slots[name] = (int(node["status"]["allocatable"]["pods"]) - len(assigned), needed, assigned)
    for name, (free, needed, assigned) in slots.items():
        if free >= needed:
            continue
        # Destino precisa manter as vagas dos seus agentes apos receber a replica.
        if not any(other != name and values[0] > values[1] for other, values in slots.items()):
            raise RuntimeError("Sem vaga de destino para redistribuir. Aumente ACADEMY_NODE_COUNT e repita up.")
        for pod in assigned:
            meta = pod["metadata"]
            ns = meta["namespace"]
            if meta.get("deletionTimestamp") or not ready(pod):
                continue
            owner = next((o for o in meta.get("ownerReferences", []) if o["kind"] == "ReplicaSet"), None)
            deployment = deployments.get((ns, owner["name"])) if owner else None
            if deployment not in OWNED.get(ns, set()):
                continue
            return {"node": name, "namespace": ns, "deployment": deployment, "pod": meta["name"],
                    "hold": [n for n, values in slots.items() if n == name or values[0] <= values[1]]}
        raise RuntimeError("No " + name + " lotado sem Deployment do projeto para redistribuir. Confira status; nao foram excluidos pods.")
    return None


if __name__ == "__main__":
    try:
        collections = [json.loads(Path(path).read_text())["items"] for path in sys.argv[1:4]]
        print(json.dumps(next_move(*collections)))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
