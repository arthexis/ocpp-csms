from __future__ import annotations

import unittest

from ocpp_csms.evidence.alerts import classify_event, classify_events


def event(action: str, payload: dict | None = None, *, kind: str = "ocpp",
          direction: str = "in", event_id: int = 1) -> dict:
    return {
        "id": event_id,
        "occurred_at": "2026-10-10T12:00:00Z",
        "charger_id": "CP001",
        "kind": kind,
        "action": action,
        "direction": direction,
        "transaction_id": None,
        "id_tag": None,
        "payload": payload or {},
    }


class AlertClassificationTests(unittest.TestCase):
    def test_emergency_stop_has_explicit_category_and_evidence(self):
        result = classify_event(event("StatusNotification", {
            "status": "Faulted", "error_code": "InternalError",
            "info": "EmergencyStop", "connector_id": 2,
        }))
        self.assertIsNotNone(result)
        self.assertEqual(result["severity"], "error")
        self.assertEqual(result["category"], "emergency_stop")
        self.assertEqual(result["connector_id"], 2)
        self.assertEqual(result["details"]["info"], "EmergencyStop")

    def test_non_emergency_charger_fault(self):
        result = classify_event(event("StatusNotification", {
            "status": "Faulted", "error_code": "GroundFailure",
        }))
        self.assertEqual(result["category"], "charger_fault")

    def test_ordinary_events_are_not_alerts(self):
        normal = [
            event("Heartbeat"),
            event("StartTransaction", {"connector_id": 1}),
            event("StopTransaction"),
            event("MeterValues"),
            event("StatusNotification", {"status": "Charging", "error_code": "NoError"}),
            event("Authorize", {"idTagInfo": {"status": "Accepted"}}, direction="out"),
        ]
        self.assertEqual(classify_events(normal), [])

    def test_rejected_authorization_response(self):
        result = classify_event(event("Authorize", {"idTagInfo": {"status": "Blocked"}}, direction="out"))
        self.assertEqual(result["severity"], "warning")
        self.assertEqual(result["category"], "authorization")

    def test_no_false_positive_on_incoming_authorization(self):
        self.assertIsNone(classify_event(event("Authorize", {"idTagInfo": {"status": "Blocked"}})))

    def test_repeated_alerts_remain_separate(self):
        rows = [event("StatusNotification", {"status": "Faulted"}, event_id=i) for i in (10, 11)]
        self.assertEqual([a["source_event_id"] for a in classify_events(rows)], [10, 11])

    def test_malformed_payload_is_not_a_false_positive(self):
        row = event("StatusNotification")
        row["payload"] = "{not-json"
        self.assertIsNone(classify_event(row))

    def test_runtime_transition(self):
        result = classify_event(event("charger_disconnected", kind="runtime"))
        self.assertEqual((result["severity"], result["category"]), ("warning", "connectivity"))


if __name__ == "__main__":
    unittest.main()
