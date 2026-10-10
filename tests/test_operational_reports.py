from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.reports import build_report, format_report


class OperationalReportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = EventStore(self.root)
        self.now = datetime.now(timezone.utc)

    def _transaction(self, tid, tag, start, stop):
        payload = {"connector_id": 1, "id_tag": tag, "meter_start": start,
                   "timestamp": self.now.isoformat()}
        self.store.record_transaction_start(tid, "CP001", payload)
        if stop is not None:
            self.store.record_transaction_stop("CP001", {
                "transaction_id": tid, "meter_stop": stop,
                "timestamp": (self.now + timedelta(minutes=10)).isoformat()
            })

    def test_rfid_and_period_subtotal(self):
        self._transaction(101, "RFID-A", 1000, 6200)
        self._transaction(102, "RFID-B", 1000, None)
        report = build_report(self.root, since=self.now - timedelta(hours=1),
                              until=self.now + timedelta(hours=1))
        data = report["data"]
        self.assertEqual(report["schema"], "ocpp-csms/report/v1")
        self.assertEqual([t["rfid"] for t in data["transactions"]], ["RFID-A", "RFID-B"])
        self.assertEqual(data["subtotal"]["transactions"], 2)
        self.assertEqual(data["subtotal"]["measured"], 1)
        self.assertEqual(data["subtotal"]["energy_kwh"], 5.2)
        text = format_report(report)
        self.assertIn("SUBTOTAL", text)
        self.assertIn("RFID-B", text)
        self.assertIn("(1/2 measured)", text)

    def test_charger_filter(self):
        self._transaction(103, "CARD", 0, 2000)
        report = build_report(self.root, since=self.now - timedelta(hours=1),
                              until=self.now + timedelta(hours=1), charger_id="OTHER")
        self.assertEqual(report["data"]["subtotal"]["transactions"], 0)

    def test_empty_report(self):
        result = build_report(self.root, since=self.now - timedelta(days=1), until=self.now)
        self.assertEqual(result["data"]["subtotal"]["transactions"], 0)
        self.assertEqual(result["data"]["subtotal"]["energy_kwh"], 0)

    def test_invalid_period(self):
        with self.assertRaises(ValueError):
            build_report(self.root, since=self.now, until=self.now - timedelta(days=1))


if __name__ == "__main__":
    unittest.main()
