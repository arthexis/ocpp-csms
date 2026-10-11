from __future__ import annotations

import json

import pytest

import ocpp_csms.cli.config as config_cli
import ocpp_csms.cli.profile as profile_cli
from ocpp_csms.cli import build_parser
from ocpp_csms.cli.config import run_config_download
from ocpp_csms.cli.json_contracts import run_transactions_json
from ocpp_csms.cli.profile import run_profile
from ocpp_csms.output import emit_json, json_command_result


def test_json_read_commands_are_registered():
    parser, _ = build_parser()
    assert parser.parse_args(["status", "--json"]).json is True
    assert parser.parse_args(["events", "--json"]).json is True
    assert parser.parse_args(["transactions", "--json"]).json is True
    assert parser.parse_args(["txn", "-j"]).json is True
    assert parser.parse_args(["energy", "-j"]).json is True
    assert parser.parse_args(["energy", "-c", "2"]).connector == 2
    assert parser.parse_args(["export", "--after", "12", "--limit", "50", "--json"]).after == 12
    assert parser.parse_args(["config", "download", "--json"]).json is True
    assert parser.parse_args(["profile", "composite", "--json"]).json is True


def test_events_transaction_alias_and_raw_mode_are_registered():
    parser, _ = build_parser()
    args = parser.parse_args(["events", "--txn", "42", "--raw", "--json"])
    assert args.transaction == 42
    assert args.raw is True
    assert args.json is True


@pytest.mark.parametrize("argv,field,expected", [
    (("events", "--follow", "--json"), "follow", True),
    (("events", "-f", "-j"), "follow", True),
    (("reservation", "list", "--cp", "CP1", "--all", "-j"), "reservation_action", "list"),
    (("firmware", "check", "--cp", "CP1", "--offline", "-j"), "firmware_action", "check"),
    (("firmware", "update", "--location", "https://example.org/fw.bin", "--after", "60", "--confirm"), "after", 60),
])
def test_recent_cli_parser_contracts(parse_cli, argv, field, expected):
    args = parse_cli(*argv)
    assert getattr(args, field) == expected


def test_shared_json_emitter_is_deterministic(capsys):
    emit_json(json_command_result({"b": 2, "a": 1}, schema="example/v1"))
    output = capsys.readouterr().out
    assert output.endswith("\n")
    assert json.loads(output) == {"schema": "example/v1", "data": {"a": 1, "b": 2}}
    assert output.index('"a"') < output.index('"b"')


def test_config_download_json_uses_deterministic_emitter(monkeypatch, tmp_path, capsys):
    async def fake_send_control(data_dir, request):
        return {
            "ok": True,
            "response": {
                "configuration_key": [
                    {"key": "HeartbeatInterval", "readonly": False, "value": "300"},
                ],
                "unknown_key": [],
            },
        }

    monkeypatch.setattr(config_cli, "send_control", fake_send_control)
    parser, _ = build_parser()
    args = parser.parse_args(["--data-dir", str(tmp_path), "config", "download", "charger-a", "--json"])
    assert run_config_download(args) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["charger"] == "charger-a"


def test_profile_composite_json_uses_deterministic_emitter(monkeypatch, tmp_path, capsys):
    async def fake_send_control(data_dir, request):
        return {
            "ok": True,
            "response": {
                "status": "Accepted",
                "connector_id": 0,
                "schedule_start": "2026-10-03T20:00:00Z",
                "charging_schedule": {
                    "chargingRateUnit": "W",
                    "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 60000}],
                },
            },
        }

    monkeypatch.setattr(profile_cli, "send_control", fake_send_control)
    parser, _ = build_parser()
    args = parser.parse_args(["--data-dir", str(tmp_path), "profile", "composite", "--json"])
    assert run_profile(args) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "Accepted"


def test_transaction_json_local_time_controls_filter_and_event_times(tmp_path, capsys):
    directory = tmp_path / "transactions" / "2026-10-06"
    directory.mkdir(parents=True)
    record = {
        "transaction_id": 1,
        "origin": "local",
        "charge_point_id": "charger-a",
        "status": "stopped",
        "created_at": "2026-10-06T12:00:00Z",
        "updated_at": "2026-10-06T12:10:00Z",
        "start": {"connector_id": 1, "timestamp": "2020-01-01T00:00:00Z"},
        "start_received_at": "2026-10-06T12:00:00Z",
        "meter_values": [],
        "meter_values_received_at": [],
        "stop": {"transaction_id": 1, "timestamp": "2020-01-01T00:10:00Z"},
        "stop_received_at": "2026-10-06T12:10:00Z",
    }
    (directory / "charger-a-1.json").write_text(json.dumps(record))

    parser, _ = build_parser()
    args = parser.parse_args([
        "--data-dir", str(tmp_path), "txn", "-jT", "--since", "2026-10-06T12:05:00Z",
    ])
    assert run_transactions_json(args) == 0
    payload = json.loads(capsys.readouterr().out)
    [transaction] = payload["data"]["transactions"]
    assert transaction["started_at"] == "2026-10-06T12:00:00Z"
    assert transaction["stopped_at"] == "2026-10-06T12:10:00Z"
    assert transaction["last_activity_at"] == "2026-10-06T12:10:00Z"


def test_transaction_json_no_limit_forwards_unbounded_query(monkeypatch, capsys):
    from ocpp_csms.transactions.query import TransactionQuery

    observed = []
    monkeypatch.setattr(TransactionQuery, "list", lambda self, **kw: observed.append(kw) or [])
    parser, _ = build_parser()
    args = parser.parse_args(["txn", "--since", "3d", "-N", "--json"])
    assert run_transactions_json(args) == 0
    assert observed[0]["limit"] is None
    assert observed[0]["since"] is not None
    assert json.loads(capsys.readouterr().out)["schema"] == "ocpp-csms/transactions/v1"
