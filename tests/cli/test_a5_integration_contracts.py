"""A5.3: CLI entrypoint integration contracts without runtime changes."""
from __future__ import annotations

import json
import sys

import pytest

from ocpp_csms.cli import build_parser, main
from ocpp_csms.cli.control import control_request
from ocpp_csms.cli.transactions import build_txn_operations_parser


@pytest.mark.parametrize(("argv", "expected"), [
    (["txn", "start", "--cp", "CP1", "-c", "2", "--rfid", "ABC"], {"command": "start", "charger": "CP1", "connector": 2, "id_tag": "ABC", "timing": "now"}),
    (["txn", "stop", "42", "--after", "10"], {"command": "stop", "transaction": 42, "timing": "after", "seconds": 10}),
])
def test_canonical_txn_entrypoint_preserves_control_payload(monkeypatch, tmp_path, argv, expected):
    observed = []
    monkeypatch.setattr("ocpp_csms.cli.run_control", lambda args: observed.append(control_request(args)) or 0)
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "--data-dir", str(tmp_path), *argv])
    assert main() == 0
    assert observed == [expected]


@pytest.mark.parametrize("argv", [
    ["txn", "stop", "42", "--txn", "43"],
    ["txn", "start", "CP1", "--cp", "CP2"],
    ["txn", "explain", "-1"],
])
def test_invalid_txn_entrypoint_exits_with_usage_error(monkeypatch, tmp_path, argv):
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "--data-dir", str(tmp_path), *argv])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_reserve_detail_json_remains_offline_and_filters_exact_id(monkeypatch, tmp_path, capsys):
    observed = []
    rows = [
        {"id": 41, "cp": "CP1", "connector": 1, "rfid": "A", "requested": "2026-10-01", "expires": None, "status": "Accepted"},
        {"id": 42, "cp": "CP1", "connector": 1, "rfid": "B", "requested": "2026-10-01", "expires": None, "status": "Canceled"},
    ]
    def fake_list(data_dir, **kwargs):
        observed.append((data_dir, kwargs))
        return rows
    monkeypatch.setattr("ocpp_csms.cli.reservation.list_reservations", fake_list)
    monkeypatch.setattr("ocpp_csms.cli.reservation.send_control", lambda *a, **k: pytest.fail("read-only reserve queried charger"))
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "--data-dir", str(tmp_path), "reserve", "42", "--cp", "CP1", "-j"])
    assert main() == 0
    assert [row["id"] for row in json.loads(capsys.readouterr().out)] == [42]
    assert observed == [(str(tmp_path), {"charger": "CP1", "include_all": True, "since": None})]


def test_txn_json_schema_stays_stable(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "--data-dir", str(tmp_path), "txn", "-j"])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "ocpp-csms/transactions/v1"
    assert isinstance(payload["data"]["transactions"], list)


def test_export_command_stays_registered_and_schema_constant():
    from ocpp_forwarder.exporter import EXPORT_SCHEMA
    parser, commands = build_parser()
    assert "export" in commands
    assert parser.parse_args(["export", "--after", "12", "--limit", "20", "-j"]).command == "export"
    assert EXPORT_SCHEMA == "ocpp-csms/export/v1"


def test_txn_operation_parser_is_independent_of_read_only_query():
    parser, _ = build_parser()
    assert parser.parse_args(["txn", "42"]).transaction_id == 42
    assert build_txn_operations_parser().parse_args(["stop", "42"]).transaction_id == 42
