"""Webhook interno do Grafana: incidente, ChatOps e restart restrito ao evaluation."""
import datetime
import hmac
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN_PATH = Path('/var/run/secrets/kubernetes.io/serviceaccount')
DEPLOYMENT_PATH = '/apis/apps/v1/namespaces/toggle-master/deployments/evaluation-service'
DEDUP_KEY = 'togglemaster-evaluation-errors'
lock = threading.Lock()
last_restart = 0.0


def emit(event, **fields):
    print(json.dumps({'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      'event': event, **fields}), flush=True)


def request_json(url, body, headers=None, context=None, method='POST'):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Content-Type': 'application/json',
                                          'User-Agent': 'ToggleMaster/1.0', **(headers or {})})
    with urllib.request.urlopen(req, timeout=8, context=context) as response:
        return response.status


def restart_evaluation():
    ca = ssl.create_default_context(cafile=str(TOKEN_PATH / 'ca.crt'))
    token = (TOKEN_PATH / 'token').read_text().strip()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    host = os.environ.get('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')
    port = os.environ.get('KUBERNETES_SERVICE_PORT_HTTPS', '443')
    # O destino e o corpo sao fixos; o webhook nao escolhe namespace nem comando.
    return request_json(f'https://{host}:{port}{DEPLOYMENT_PATH}',
                        {'spec': {'template': {'metadata': {'annotations': {
                            'kubectl.kubernetes.io/restartedAt': now}}}}},
                        {'Authorization': 'Bearer ' + token,
                         'Content-Type': 'application/strategic-merge-patch+json'}, ca, 'PATCH')


def notify(status, outcome='pending'):
    if os.environ.get('EXTERNAL_INTEGRATIONS_ENABLED', 'true') == 'false':
        emit('notification_disabled', status=status, reason='local lab without external integrations')
        return
    pd_body = {'routing_key': os.environ['PAGERDUTY_ROUTING_KEY'],
               'event_action': 'trigger' if status == 'firing' else 'resolve',
               'dedup_key': DEDUP_KEY}
    if status == 'firing':
        pd_body['payload'] = {'summary': 'ToggleMaster: evaluation com mais de 5% de erros HTTP 5xx',
                              'source': os.environ.get('CLUSTER_NAME', 'toggle-cluster'), 'severity': 'critical',
                              'component': 'evaluation-service',
                              'custom_details': {'window': '2m', 'pending': '45s',
                                                 'runbook': 'Reiniciar deployment/evaluation-service uma vez; verificar recuperacao.'}}
    deliveries = [
        ('pagerduty', 'https://events.pagerduty.com/v2/enqueue', pd_body),
        ('discord', os.environ['DISCORD_WEBHOOK_URL'], {'content': (
            f'**ToggleMaster | {status.upper()}**\n'
            f"Servico: evaluation-service | Cluster: {os.environ.get('CLUSTER_NAME', 'toggle-cluster')}\n"
            'Regra: erros HTTP 5xx > 5%, janela 2m, pendencia 45s.\n'
            f'Runbook automatico: reiniciar somente evaluation-service. Resultado: {outcome}.\n'
            f'Correlacao do incidente: {DEDUP_KEY}')})]
    for name, url, body in deliveries:
        for attempt in range(3):
            try:
                code = request_json(url, body)
                emit('notification_sent', integration=name, status=status, http_status=code)
                break
            except (OSError, ValueError) as exc:
                # Nao registrar URL, corpo ou texto da excecao: podem conter tokens.
                emit('notification_failed', integration=name, status=status, attempt=attempt + 1,
                     http_status=exc.code if isinstance(exc, urllib.error.HTTPError) else None,
                     error_type=type(exc).__name__)
                if attempt < 2:
                    time.sleep(1)


def handle_alert(payload):
    global last_restart
    alerts = [a for a in payload.get('alerts', [])
              if a.get('labels', {}).get('alertname') == 'EvaluationHighErrorRate'
              and a.get('labels', {}).get('service') == 'evaluation-service']
    if not alerts:
        emit('alert_ignored')
        return
    status = 'firing' if any(a.get('status') == 'firing' for a in alerts) else 'resolved'
    with lock:
        if status == 'resolved':
            notify(status, 'alerta normalizado; verificar pods e resposta HTTP')
            emit('alert_resolved')
            return
        if last_restart > 0 and time.monotonic() - last_restart < 600:
            emit('restart_skipped_cooldown', seconds=600)
            return
        emit('alert_received', service='evaluation-service', status=status)
        notify(status, 'iniciado')
        try:
            code = restart_evaluation()
            last_restart = time.monotonic()
            emit('restart_requested', deployment='evaluation-service', http_status=code)
            # O PATCH aceito nao comprova que o rollout terminou.
        except (OSError, ValueError):
            emit('restart_failed', deployment='evaluation-service')
            notify(status, 'falha ao solicitar restart; intervencao necessaria')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200 if self.path == '/health' else 404)
        self.end_headers()

    def do_POST(self):
        expected = os.environ.get('HEAL_TOKEN', '')
        if self.path != '/alert':
            self.send_error(404)
            return
        if not expected or not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + expected):
            self.send_error(401)
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 65536:
                raise ValueError('size')
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict) or not isinstance(payload.get('alerts'), list):
                raise ValueError('payload')
        except (ValueError, TypeError):
            self.send_error(400)
            return
        # Mantem a resposta do webhook rapida; apenas um runbook pode executar por vez.
        threading.Thread(target=handle_alert, args=(payload,), daemon=True).start()
        self.send_response(202)
        self.end_headers()


if __name__ == '__main__':
    for key in ('HEAL_TOKEN', 'PAGERDUTY_ROUTING_KEY', 'DISCORD_WEBHOOK_URL'):
        if not os.environ.get(key):
            raise SystemExit('Secret incompleto: ' + key)
    emit('self_healer_started', target='toggle-master/evaluation-service', cooldown_seconds=600)
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
