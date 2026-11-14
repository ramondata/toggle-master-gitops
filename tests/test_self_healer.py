import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

path = Path(__file__).resolve().parents[1] / 'apps/observability-telemetry/files/self_healer.py'
spec = importlib.util.spec_from_file_location('healer', path)
healer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(healer)


def alert(status='firing', service='evaluation-service'):
    return {'alerts': [{'status': status, 'labels': {
        'alertname': 'EvaluationHighErrorRate', 'service': service}}]}


class SelfHealingTests(unittest.TestCase):
    def setUp(self):
        healer.last_restart = 0

    @patch.dict('os.environ', {'EXTERNAL_INTEGRATIONS_ENABLED': 'false'})
    @patch.object(healer, 'request_json')
    def test_local_mode_does_not_send_external_notifications(self, request):
        healer.notify('firing')
        healer.notify('resolved')
        request.assert_not_called()

    @patch.object(healer, 'notify')
    @patch.object(healer, 'restart_evaluation', return_value=200)
    def test_repeated_firing_restarts_only_once(self, restart, notify):
        healer.handle_alert(alert())
        healer.handle_alert(alert())
        restart.assert_called_once_with()
        notify.assert_called_once_with('firing', 'iniciado')

    @patch.object(healer, 'notify')
    @patch.object(healer, 'restart_evaluation')
    def test_resolved_does_not_restart(self, restart, notify):
        healer.handle_alert(alert('resolved'))
        restart.assert_not_called()
        self.assertEqual(notify.call_args.args[0], 'resolved')

    @patch.object(healer, 'notify')
    @patch.object(healer, 'restart_evaluation')
    def test_unrelated_service_cannot_select_target(self, restart, notify):
        healer.handle_alert(alert(service='argocd-server'))
        restart.assert_not_called()
        notify.assert_not_called()

    @patch.object(healer, 'notify')
    @patch.object(healer, 'restart_evaluation', side_effect=OSError)
    def test_failed_restart_allows_later_retry(self, restart, notify):
        healer.handle_alert(alert())
        self.assertEqual(healer.last_restart, 0)
        self.assertEqual(notify.call_count, 2)

    @patch.dict('os.environ', {'PAGERDUTY_ROUTING_KEY': 'test', 'DISCORD_WEBHOOK_URL': 'https://discord.com/test'})
    @patch.object(healer.time, 'sleep')
    @patch.object(healer, 'request_json', side_effect=OSError)
    def test_notification_errors_are_bounded(self, request, sleep):
        healer.notify('firing')
        self.assertEqual(request.call_count, 6)

    @patch.object(healer.urllib.request, 'urlopen')
    def test_discord_transport_includes_user_agent(self, urlopen):
        def discord_response(req, **kwargs):
            if req.get_header('User-agent') != 'ToggleMaster/1.0':
                raise healer.urllib.error.HTTPError(req.full_url, 403, 'Forbidden', {}, None)
            self.assertEqual(req.get_method(), 'POST')
            self.assertEqual(healer.json.loads(req.data), {'content': 'test'})
            response = MagicMock()
            response.__enter__.return_value.status = 204
            return response
        urlopen.side_effect = discord_response
        self.assertEqual(healer.request_json('https://discord.com/test', {'content': 'test'}), 204)

    @patch.dict('os.environ', {'PAGERDUTY_ROUTING_KEY': 'private-key', 'DISCORD_WEBHOOK_URL': 'https://discord.com/api/webhooks/private-token'})
    @patch.object(healer.time, 'sleep')
    @patch.object(healer, 'emit')
    @patch.object(healer, 'request_json')
    def test_notification_error_logs_status_without_credentials(self, request, emit, sleep):
        request.side_effect = healer.urllib.error.HTTPError(
            'https://discord.com/api/webhooks/private-token', 403, 'private-key', {}, None)
        healer.notify('firing')
        self.assertEqual(request.call_count, 6)
        for call in emit.call_args_list:
            self.assertEqual(call.kwargs['http_status'], 403)
            self.assertEqual(call.kwargs['error_type'], 'HTTPError')
            self.assertNotIn('private-token', str(call))
            self.assertNotIn('private-key', str(call))
