from __future__ import annotations

import argparse
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from ocpp_csms.cli import build_parser
from ocpp_csms.cli.diagnostics import run_alerts


class AlertsCliTests(unittest.TestCase):
    def setUp(self):
        self.parser, _ = build_parser()

    def test_filter_parsing(self):
        args = self.parser.parse_args(["alerts", "--cp", "CP001", "--since", "3d", "-N", "--txn", "44"])
        self.assertEqual(args.cp, "CP001")
        self.assertEqual(args.since, "3d")
        self.assertTrue(args.no_limit)
        self.assertEqual(args.transaction, 44)

    def test_count_applies_after_classification(self):
        rows = [
            dict(id=1, occurred_at="2026-10-10T08:00:00Z", charger_id="A", kind="runtime",
                 action="charger_disconnected", direction=None, transaction_id=None, id_tag=None, payload="{}"),
            dict(id=2, occurred_at="2026-10-10T08:01:00Z", charger_id="A", kind="ocpp",
                 action="Heartbeat", direction="in", transaction_id=None, id_tag=None, payload="{}"),
            dict(id=3, occurred_at="2026-10-10T08:02:00Z", charger_id="A", kind="runtime",
                 action="charger_disconnected", direction=None, transaction_id=None, id_tag=None, payload="{}"),
        ]
        args = self.parser.parse_args(["alerts", "-n", "2", "--json"])
        output = io.StringIO()
        with patch("ocpp_csms.cli.diagnostics.events_between", return_value=rows) as get_rows:
            with contextlib.redirect_stdout(output):
                self.assertEqual(run_alerts(args), 0)
        self.assertIsNone(get_rows.call_args.kwargs["limit"])
        data = json.loads(output.getvalue())
        self.assertEqual([a["source_event_id"] for a in data["alerts"]], [1, 3])

    def test_no_matching_alerts(self):
        args = self.parser.parse_args(["alerts"])
        output = io.StringIO()
        with patch("ocpp_csms.cli.diagnostics.events_between", return_value=[]):
            with contextlib.redirect_stdout(output):
                run_alerts(args)
        self.assertEqual(output.getvalue().strip(), "No matching alerts.")


if __name__ == "__main__":
    unittest.main()
