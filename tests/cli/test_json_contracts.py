from __future__ import annotations

import json

from ocpp_csms.cli import build_parser
from ocpp_csms.output import emit_json, json_command_result


def test_json_read_commands_are_registered():
    parser, _ = build_parser()
    assert parser.parse_args(["status", "--json"]).json is True
    assert parser.parse_args(["events", "--json"]).json is True
    assert parser.parse_args(["transactions", "--json"]).json is True
    assert parser.parse_args(["txn", "-j"]).json is True
    assert parser.parse_args(["energy", "-j"]).json is True


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
