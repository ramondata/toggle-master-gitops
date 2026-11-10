import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("finish", Path(__file__).with_name("finish.py"))
finish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finish)


class FinalChecks(unittest.TestCase):
    def test_restart_and_http_alone_do_not_pass_external_checks(self):
        events = [{"event": "restart_requested"}]
        result = finish.assess(events, True, True, True)
        self.assertFalse(all(result.values()))
        self.assertFalse(result["pagerduty_event_api_accepted"])

    def test_resolved_or_failed_notifications_do_not_count_as_incident(self):
        events = [{"event": "notification_sent", "integration": name, "status": status, "http_status": code}
                  for name, status, code in [("pagerduty", "resolved", 202), ("discord", "firing", 400)]]
        result = finish.assess(events, True, True, True)
        self.assertFalse(result["pagerduty_event_api_accepted"])
        self.assertFalse(result["discord_webhook_api_accepted"])

    def test_complete_automatic_cycle(self):
        events = [{"event": "alert_received", "status": "firing"}, {"event": "restart_requested"}]
        events += [{"event": "notification_sent", "integration": name, "status": "firing", "http_status": code}
                   for name, code in [("pagerduty", 202), ("discord", 204)]]
        self.assertTrue(all(finish.assess(events, True, True, True).values()))
        self.assertFalse(all(finish.assess(events, False, True, True).values()))

    def test_evidence_filters_unexpected_fields(self):
        rows = finish.events_from('not json\n{"event":"notification_sent","integration":"discord","secret":"never-save"}\n')
        self.assertNotIn("secret", rows[0])


if __name__ == "__main__":
    unittest.main()
