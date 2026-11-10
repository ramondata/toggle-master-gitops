#!/usr/bin/env python3
"""Prova operacional no EKS; APIs aceitarem eventos nao prova a evidencia visual."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {"alert_received", "restart_requested", "restart_failed", "notification_sent", "notification_failed", "alert_resolved", "restart_skipped_cooldown"}


def run(*args):
    return subprocess.check_output(list(args), text=True, timeout=180)


def kubectl(*args):
    return run("kubectl", *args)


def events_from(text):
    events = []
    for line in text.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("event") in ALLOWED:
            events.append({k: obj[k] for k in ("time", "event", "integration", "status", "http_status", "deployment", "service", "attempt") if k in obj})
    return events


def assess(events, saw_error, restarted, recovered):
    def sent(name):
        return any(e.get("event") == "notification_sent" and e.get("integration") == name
                   and e.get("status") == "firing" and 200 <= e.get("http_status", 0) < 300 for e in events)
    return {
        "http_5xx_observed": saw_error,
        "grafana_firing_webhook_received": any(e.get("event") == "alert_received" and e.get("status") == "firing" for e in events),
        "pagerduty_event_api_accepted": sent("pagerduty"),
        "discord_webhook_api_accepted": sent("discord"),
        "automatic_restart_requested": any(e.get("event") == "restart_requested" for e in events),
        "deployment_restart_observed": restarted,
        "http_200_after_rollout": recovered,
    }


def annotation():
    obj = json.loads(kubectl("-n", "toggle-master", "get", "deployment", "evaluation-service", "-o", "json"))
    return obj["spec"]["template"]["metadata"].get("annotations", {}).get("kubectl.kubernetes.io/restartedAt", "")


def load_rows():
    text = kubectl("-n", "toggle-master", "logs", "job/fase4-demo", "--tail=600")
    rows = []
    for line in text.splitlines():
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except ValueError:
            pass
    return rows


def main():
    os.umask(0o077)
    subprocess.run(["bash", str(ROOT / "academy/lab.sh"), "verify"], check=True)
    old_job = kubectl("-n", "toggle-master", "get", "job", "fase4-demo", "--ignore-not-found", "-o", "json")
    if old_job.strip():
        job = json.loads(old_job)
        done = any(c.get("type") in ("Complete", "Failed") and c.get("status") == "True" for c in job.get("status", {}).get("conditions", []))
        if not done:
            raise RuntimeError("Ja existe carga ativa. Aguarde terminar antes de executar finish novamente.")
        kubectl("-n", "toggle-master", "delete", "job", "fase4-demo", "--cascade=foreground", "--wait=true")
    started = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    account = json.loads(run("aws", "sts", "get-caller-identity", "--output", "json"))["Account"]
    state = Path(os.getenv("ACADEMY_STATE_DIR", str(Path.home() / ".toggle-academy" / account / os.getenv("TARGET_CLUSTER", "toggle-cluster"))))
    out = state / "evidence" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, mode=0o700)
    report = {"started_at": started, "base_verify_passed": True, "automatic_checks": {}, "manual_evidence_required": [
        "New Relic: trace distribuido e Service Map dos cinco servicos",
        "Grafana: dashboard com dados, logs e alerta Firing",
        "PagerDuty: incidente realmente aberto; Discord: mensagem recebida",
        "Quatro screenshots obrigatorios, video ate 25 minutos e relatorio PDF",
    ]}
    armed = False
    success = False
    try:
        print("Iniciando carga de teste no EKS...", flush=True)
        subprocess.run(["bash", str(ROOT / "academy/lab.sh"), "demo", "start"], check=True)
        baseline = []
        for _ in range(60):
            pods = json.loads(kubectl("-n", "toggle-master", "get", "pods", "-l", "job-name=fase4-demo", "-o", "json"))
            if any(p.get("status", {}).get("phase") == "Running" for p in pods["items"]):
                baseline = load_rows()
                if any(r.get("status") == 200 for r in baseline):
                    break
            time.sleep(2)
        else:
            raise RuntimeError("Carga nao confirmou HTTP 200 inicial; nenhuma falha foi injetada.")
        report["trace_ids"] = list(dict.fromkeys(r["trace_id"] for r in baseline if r.get("trace_id")))[:10]
        before = annotation()
        baseline_index = max(r.get("request", -1) for r in baseline)
        armed = True
        subprocess.run(["bash", str(ROOT / "academy/lab.sh"), "demo", "fail"], check=True)
        saw_error = False
        restarted = False
        events = []
        for attempt in range(120):
            rows = load_rows()
            saw_error |= any(isinstance(r.get("status"), int) and 500 <= r["status"] < 600 and r.get("request", -1) > baseline_index for r in rows)
            events = events_from(kubectl("-n", "observability", "logs", "deployment/self-healer", "--since-time=" + started))
            restarted = bool(annotation() and annotation() != before)
            report["automatic_checks"] = assess(events, saw_error, restarted, False)
            if restarted:
                break
            if attempt % 10 == 0:
                print("Aguardando alerta e restart automatico...", flush=True)
            time.sleep(3)
        if not restarted:
            raise RuntimeError("Restart automatico nao confirmado em 6 minutos. Confira alerta, cooldown de 10min e logs do healer.")
        # Somente observacao: nao executa restart manual.
        kubectl("-n", "toggle-master", "rollout", "status", "deployment/evaluation-service", "--timeout=120s")
        end_index = max(r.get("request", -1) for r in load_rows())
        recovered = False
        for _ in range(20):
            rows = load_rows()
            if any(r.get("status") == 200 and r.get("request", -1) > end_index for r in rows):
                recovered = True
                break
            time.sleep(2)
        events = events_from(kubectl("-n", "observability", "logs", "deployment/self-healer", "--since-time=" + started))
        report["automatic_checks"] = assess(events, saw_error, restarted, recovered)
        (out / "self-healing.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        (out / "load.jsonl").write_text("".join(json.dumps({k: r[k] for k in ("request", "status", "trace_id") if k in r}) + "\n" for r in rows))
        success = all(report["automatic_checks"].values())
        if not success:
            raise RuntimeError("Fluxo incompleto. Confira os itens false no resumo; aceite de API nao comprova entrega visual.")
        print("Verificacoes automaticas passaram. Confirme as telas externas antes de gravar.", flush=True)
    finally:
        report["automatic_checks_passed"] = success
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (out / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print("Resumo privado: " + str(out / "summary.json"), flush=True)
        if armed and not success:
            print("Teste falhou; desativando a falha por recuperacao de emergencia. Isso NAO conta como self-healing.", flush=True)
            subprocess.run(["bash", str(ROOT / "academy/lab.sh"), "demo", "recover"], check=False)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else "Comando falhou; confira a saida anterior no seu terminal.", file=sys.stderr)
        sys.exit(1)
