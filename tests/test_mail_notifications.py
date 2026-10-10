from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.notifications import OUTBOX, collect, deliver, duration_seconds, history, policy


TOML = """
[mail]
enabled = true
from = "csms@example.com"
to = ["admin@example.com"]
[mail.smtp]
host = "smtp.example.com"
starttls = true
[mail.alerts]
enabled = true
minimum_severity = "warning"
cooldown = "10m"
[mail.events.transaction_started]
enabled = true
[mail.events.transaction_stopped]
enabled = true
"""


class NotificationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "mail.toml"
        self.config.write_text(TOML)
        self.events = EventStore(self.root)
        collect(self.root, self.config)  # Mark prior history as already observed.

    def test_policy(self):
        self.assertEqual(duration_seconds("10m"), 600)
        self.assertTrue(policy(self.config)["subscriptions"]["transaction_started"])
        with self.assertRaises(ValueError):
            duration_seconds("0m")

    def test_repeated_fault_cooldown_and_history(self):
        for _ in range(2):
            self.events.record_ocpp("CP1", "StatusNotification",
                                    {"status": "Faulted", "error_code": "GroundFailure",
                                     "connector_id": 1})
        collect(self.root, self.config)
        records = history(self.root)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "alert")

    def test_transaction_start_no_cooldown(self):
        for tid in (301, 302):
            payload = {"connector_id": 1, "id_tag": "A", "meter_start": 10,
                       "timestamp": datetime.now(timezone.utc).isoformat()}
            self.events.record_transaction_start(tid, "CP1", payload)
            self.events.record_ocpp("CP1", "StartTransaction", payload, transaction_id=tid)
        collect(self.root, self.config)
        self.assertEqual(len([r for r in history(self.root) if r["kind"] == "transaction_started"]), 2)
        collect(self.root, self.config)
        self.assertEqual(len(history(self.root)), 2)

    def test_pending_mail_retries_and_delivery(self):
        self.events.record_runtime("charger_disconnected", charger_id="CP1")
        collect(self.root, self.config)
        with patch("ocpp_csms.notifications.send_message", side_effect=OSError("secret")):
            deliver(self.root, self.config)
        records = history(self.root)
        self.assertEqual(records[0]["attempts"], 1)
        self.assertNotIn("secret", repr(records))
        with sqlite3.connect(self.root / OUTBOX) as db:
            db.execute("UPDATE messages SET due_at='2000-01-01T00:00:00+00:00'")
        with patch("ocpp_csms.notifications.send_message") as send:
            deliver(self.root, self.config)
        send.assert_called_once()
        self.assertEqual(history(self.root)[0]["state"], "sent")


if __name__ == "__main__":
    unittest.main()
