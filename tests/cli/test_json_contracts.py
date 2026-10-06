from __future__ import annotations

import json

import ocpp_csms.cli.config as config_cli
import ocpp_csms.cli.profile as profile_cli
from ocpp_csms.cli import build_parser
from ocpp_csms.cli.config import run_config_download
from ocpp_csms.cli.profile import run_profile
from ocpp_csms.output import emit_json, json_command_result


def test_json_read_commands_are_registered():
    parser, _ = build_parser()
    assert parser.parse_args(["status", "--json"]).json is True
    assert parser.parse_args(["events", "--json"]).json is True
    assert parser.parse_args(["transactions", "--json"]).json is True
    assert parser.parse_args(["txn", "-j"]).json is True
    assert parser.parse_args(["energy", "-j"]).json is True
    assert parser.parse_args(["config", "download", "--json"]).json is True
    assert parser.parse_args(["profile", "composite", "--json"]).json is True


def test_events_transaction_alias_and_raw_mode_are_registered():
    parser, _ = build_parser()
    args = parser.parse_args(["events", "--txn", "42", "--raw", "--json"])
    assert args.transaction == 42
    assert args.raw is True
    assert args.json is True


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
    assert output.endswith("\n")
    assert json.loads(output)["charger"] == "charger-a"
    assert "\n  " not in output


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
    assert output.endswith("\n")
    assert json.loads(output)["status"] == "Accepted"
    assert "\n  " not in output
