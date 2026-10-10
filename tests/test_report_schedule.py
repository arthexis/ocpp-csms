from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.report_schedule import enqueue_scheduled
from ocpp_csms.notifications import OUTBOX

BASE = """
[mail]
enabled = true
from = "csms@example.com"
to = ["admin@example.com"]
[mail.smtp]
host = "smtp.example.com"
starttls = true
[mail.reports.daily]
enabled = true
time = "08:00"
timezone = "America/Monterrey"
[mail.reports.weekly]
enabled = true
weekday = "monday"
time = "08:00"
timezone = "America/Monterrey"
"""


class ScheduledReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        EventStore(self.root)
        self.config = self.root / "mail.toml"
        self.config.write_text(BASE)

    def test_queue_once_per_period(self):
        now = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)  # Monday 09:00 Monterrey
        self.assertEqual(enqueue_scheduled(self.root, self.config, now=now), 2)
        self.assertEqual(enqueue_scheduled(self.root, self.config, now=now), 0)
        with sqlite3.connect(self.root / OUTBOX) as db:
            rows = db.execute("SELECT identity, body FROM messages ORDER BY identity").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("SUBTOTAL" in row[1] for row in rows))
        self.assertTrue(any("report:daily:" in row[0] for row in rows))

    def test_not_due_before_local_time(self):
        now = datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc)  # Monday 07:00
        self.assertEqual(enqueue_scheduled(self.root, self.config, now=now), 2)
        with sqlite3.connect(self.root / OUTBOX) as db:
            identities = [row[0] for row in db.execute("SELECT identity FROM messages")]
        self.assertTrue(any("2026-10-10:2026-10-11" in identity for identity in identities
                            if "daily" in identity))

    def test_disabled(self):
        self.config.write_text(BASE.replace("enabled = true", "enabled = false", 1))
        self.assertEqual(enqueue_scheduled(self.root, self.config), 0)

    def test_reject_bad_weekday(self):
        self.config.write_text(BASE.replace('weekday = "monday"', 'weekday = "nope"'))
        with self.assertRaisesRegex(ValueError, "weekday"):
            enqueue_scheduled(self.root, self.config,
                              now=datetime(2026, 10, 12, 15, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
