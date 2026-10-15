"""Cross-command A4d D2 maintenance contracts at the shared transport boundary."""
from __future__ import annotations

import json

import pytest

from ocpp_csms.cli import build_parser
from ocpp_csms.cli.maintenance import maintenance_request, run_maintenance
from ocpp_csms.cli.charger_diagnostics import _request as diagnostics_request, run_diagnostics
from ocpp_csms.cli.firmware import firmware_request, run_firmware
from ocpp_csms.cli.config import configuration_request, run_configuration


def parse(*argv):
    parser, _ = build_parser()
    return parser.parse_args(["--data-dir", "/tmp", *argv])


@pytest.mark.parametrize(("argv", "builder", "expected"), [
    (("trigger", "heartbeat", "--cp", "CP1"), maintenance_request,
     {"command": "trigger", "message": "Heartbeat", "charger": "CP1"}),
    (("availability", "disable", "-c", "2"), maintenance_request,
     {"command": "availability", "connector": 2, "type": "Inoperative"}),
    (("unlock", "-c", "1"), maintenance_request, {"command": "unlock", "connector": 1}),
    (("config", "set", "HeartbeatInterval", "60"), configuration_request,
     {"command": "config_set", "key": "HeartbeatInterval", "value": "60"}),
    (("diagnose", "--location", "https://example.org/upload"), diagnostics_request,
     {"command": "get_diagnostics", "location": "https://example.org/upload"}),
])
def test_request_construction(argv, builder, expected):
    request = builder(parse(*argv))
    assert request == expected


def test_firmware_request_requires_confirmation():
    with pytest.raises(ValueError, match="confirm"):
        firmware_request(parse("firmware", "update", "--location", "https://example.org/fw"))


@pytest.mark.parametrize(("argv", "runner", "response", "exit_code"), [
    (("trigger", "heartbeat"), run_maintenance, {"status": "Accepted"}, 0),
    (("availability", "disable"), run_maintenance, {"status": "Scheduled"}, 0),
    (("unlock", "-c", "1"), run_maintenance, {"status": "Rejected"}, 1),
    (("diagnose", "--location", "https://example.org/upload"), run_diagnostics, {"file_name": "diag.log"}, 0),
    (("firmware", "update", "--location", "https://example.org/fw", "--confirm"), run_firmware, {}, 0),
    (("config",), run_configuration, {"configuration_key": [], "unknown_key": []}, 0),
])
def test_command_response_and_exit(monkeypatch, capsys, argv, runner, response, exit_code):
    requests = []
    def fake_control(_data_dir, request, **_kwargs):
        requests.append(request)
        return response
    monkeypatch.setattr(runner.__module__ + ".maintenance_control", fake_control)
    assert runner(parse(*argv)) == exit_code
    assert len(requests) == 1
    output = capsys.readouterr().out
    if argv[0] == "diagnose":
        assert "not confirmed" in output
    if argv[0] == "firmware":
        assert "not confirmed" in output
    if response.get("status") == "Scheduled":
        assert "Pending" in output


@pytest.mark.parametrize(("argv", "runner", "payload"), [
    (("trigger", "heartbeat", "-j"), run_maintenance, {"status": "Accepted"}),
    (("diagnose", "--location", "https://example.org/upload", "-j"), run_diagnostics, {"file_name": "diag.log"}),
    (("firmware", "update", "--location", "https://example.org/fw", "--confirm", "-j"), run_firmware, {}),
])
def test_json_is_single_machine_readable_object(monkeypatch, capsys, argv, runner, payload):
    monkeypatch.setattr(runner.__module__ + ".maintenance_control", lambda *_args, **_kwargs: payload)
    assert runner(parse(*argv)) == 0
    assert json.loads(capsys.readouterr().out) == payload


@pytest.mark.parametrize(("argv", "runner"), [
    (("trigger", "heartbeat"), run_maintenance),
    (("diagnose", "--location", "https://example.org/upload"), run_diagnostics),
    (("firmware", "update", "--location", "https://example.org/fw", "--confirm"), run_firmware),
    (("config",), run_configuration),
])
@pytest.mark.parametrize("error", ["not_connected", "timeout", "rejected"])
def test_command_errors_return_nonzero(monkeypatch, capsys, argv, runner, error):
    from ocpp_csms.cli.maintenance_transport import MaintenanceControlError
    def fail(*_args, **_kwargs):
        raise MaintenanceControlError(error)
    monkeypatch.setattr(runner.__module__ + ".maintenance_control", fail)
    assert runner(parse(*argv)) == 1
    assert error in capsys.readouterr().out


@pytest.mark.parametrize(("argv", "runner"), [
    (("trigger", "heartbeat"), run_maintenance),
    (("diagnose", "--location", "https://example.org/upload"), run_diagnostics),
    (("firmware", "update", "--location", "https://example.org/fw", "--confirm"), run_firmware),
    (("config",), run_configuration),
])
def test_malformed_response_is_rejected(monkeypatch, capsys, argv, runner):
    from ocpp_csms.cli.maintenance_transport import MaintenanceControlError
    def malformed(*_args, **_kwargs):
        raise MaintenanceControlError("invalid OCPP response")
    monkeypatch.setattr(runner.__module__ + ".maintenance_control", malformed)
    assert runner(parse(*argv)) == 1
    assert "invalid OCPP response" in capsys.readouterr().out
