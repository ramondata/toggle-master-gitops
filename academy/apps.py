#!/usr/bin/env python3
"""Manifests publicos do bootstrap EKS. Nenhum valor de Secret entra aqui."""
import argparse
import json
import re

REPO = "https://github.com/ramondata/toggle-master-gitops.git"
SERVICES = ("auth-service", "flag-service", "targeting-service", "evaluation-service", "analytics-service")


def application(name, ref, namespace, values=None, parameters=None, value_files=None):
    helm = {"releaseName": name, "parameters": [], "valueFiles": [], "values": ""}
    if values is not None:
        helm["valuesObject"] = values
    if parameters:
        helm["parameters"] = parameters
    if value_files:
        helm["valueFiles"] = value_files
    return {
        "apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
        "metadata": {"name": name, "namespace": "argocd"},
        "spec": {
            "project": "default",
            "source": {"repoURL": REPO, "targetRevision": ref, "path": "apps/" + name, "helm": helm},
            "destination": {"server": "https://kubernetes.default.svc", "namespace": namespace},
            "syncPolicy": {
                "automated": {"prune": True, "selfHeal": True},
                "syncOptions": ["CreateNamespace=true", "ServerSideApply=true", "PruneLast=true", "RespectIgnoreDifferences=true"],
                "retry": {"limit": 5, "backoff": {"duration": "5s", "factor": 2, "maxDuration": "2m"}},
            },
        },
    }


def services(ref, account, region, config_revision, aws_revision):
    app = application("placeholder", ref, "toggle-master")
    source = app["spec"]["source"]
    source["path"] = "apps/{{.service}}"
    source["helm"] = {"parameters": [
        {"name": "image.repository", "value": "{{.repository}}"},
        {"name": "replicaCount", "value": "1"},
        {"name": "awsRegion", "value": region},
        {"name": "academyConfigRevision", "value": config_revision, "forceString": True},
        {"name": "academyAwsRevision", "value": aws_revision, "forceString": True},
        {"name": "serviceIngressEnabled", "value": "false"},
        {"name": "hpa.maxReplicas", "value": "2"},
    ]}
    app["spec"]["ignoreDifferences"] = [
        {"group": "apps", "kind": "Deployment", "namespace": "toggle-master",
         "jsonPointers": ["/spec/template/metadata/annotations/kubectl.kubernetes.io~1restartedAt"]},
    ] + [
        {"group": "apps", "kind": "Deployment", "name": name, "namespace": "toggle-master",
         "jsonPointers": ["/spec/replicas"]}
        for name in ("evaluation-service", "analytics-service")
    ]
    return {
        "apiVersion": "argoproj.io/v1alpha1", "kind": "ApplicationSet",
        "metadata": {"name": "toggle-master-services", "namespace": "argocd"},
        "spec": {"goTemplate": True, "goTemplateOptions": ["missingkey=error"],
                 "generators": [{"list": {"elements": [
                     {"service": name, "repository": f"{account}.dkr.ecr.{region}.amazonaws.com/toggle-master/{name}"}
                     for name in ("platform",) + SERVICES
                 ]}}],
                 "template": {"metadata": {"name": "{{.service}}", "namespace": "argocd"}, "spec": app["spec"]}},
    }


def ready(app, config_revision=None, aws_revision=None):
    source = app.get("spec", {}).get("source")
    parameters = {p["name"]: p["value"] for p in (source or {}).get("helm", {}).get("parameters", [])}
    if config_revision is not None and parameters.get("academyConfigRevision") != config_revision:
        return False
    if aws_revision is not None and parameters.get("academyAwsRevision") != aws_revision:
        return False
    status = app.get("status", {})
    sync = status.get("sync", {})
    operation = status.get("operationState", {})
    return bool(source and sync.get("status") == "Synced"
                and status.get("health", {}).get("status") == "Healthy"
                and sync.get("comparedTo", {}).get("source") == source
                and operation.get("phase") == "Succeeded"
                and operation.get("syncResult", {}).get("source") == source)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["services", "metrics", "telemetry", "ready"])
    parser.add_argument("--ref", default="master")
    parser.add_argument("--account", default="")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--cluster", default="toggle-cluster")
    parser.add_argument("--host", default="grafana-lab.invalid")
    parser.add_argument("--config-revision", default="initial")
    parser.add_argument("--aws-revision", default="initial")
    parser.add_argument("--new-relic-region", choices=["US", "EU"], default="US")
    args = parser.parse_args()
    if args.mode == "ready":
        import sys
        config = None if args.config_revision == "initial" else args.config_revision
        aws = None if args.aws_revision == "initial" else args.aws_revision
        sys.exit(0 if ready(json.load(sys.stdin), config, aws) else 1)
    if args.mode == "services":
        if not re.fullmatch(r"\d{12}", args.account):
            parser.error("AWS account deve ter 12 digitos")
        result = services(args.ref, args.account, args.region, args.config_revision, args.aws_revision)
    elif args.mode == "metrics":
        result = application("observability-metrics", args.ref, "observability", values={
            "publicHost": args.host,
            "monitoring": {"grafana": {"podAnnotations": {"togglemaster.io/public-host": args.host}}},
        }, value_files=["../../academy/metrics-values.yaml"])
    else:
        endpoint = "https://otlp.nr-data.net:4318" if args.new_relic_region == "US" else "https://otlp.eu01.nr-data.net:4318"
        result = application("observability-telemetry", args.ref, "observability", values={
            "externalIntegrationsEnabled": True, "clusterName": args.cluster, "newRelicEndpoint": endpoint,
        })
    print(json.dumps(result))


if __name__ == "__main__":
    main()
