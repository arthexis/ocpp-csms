import pytest

import ocpp_csms.cli.control as control_module
from ocpp_csms.cli.control import control_request, run_control


def test_start_command_builds_control_request_with_or_without_charger(parse_cli):
    assert control_request(parse_cli("start", "--connector", "2", "--id-tag", "REMOTE")) == {
        "command": "start",
        "id_tag": "REMOTE",
        "connector": 2,
    }

    expected = {
        "command": "start",
        "charger": "charger-a",
        "id_tag": "REMOTE",
        "connector": 2,
    }
    assert control_request(parse_cli("start", "charger-a", "--connector", "2", "--id-tag", "REMOTE")) == expected
    assert control_request(parse_cli("start", "--charger", "charger-a", "--c", "2", "--id-tag", "REMOTE")) == expected


def test_stop_command_builds_control_request_with_or_without_charger(parse_cli):
    assert control_request(parse_cli("stop", "--transaction", "42")) == {
        "command": "stop",
        "transaction": 42,
    }

    expected = {
        "command": "stop",
        "charger": "charger-a",
        "transaction": 42,
    }
    assert control_request(parse_cli("stop", "charger-a", "--transaction", "42")) == expected
    assert control_request(parse_cli("stop", "--charger", "charger-a", "--txn", "42")) == expected


def test_reboot_defaults_to_soft_and_supports_explicit_charger(parse_cli):
    assert control_request(parse_cli("reboot")) == {"command": "reboot", "type": "Soft"}
    assert control_request(parse_cli("reboot", "--hard")) == {"command": "reboot", "type": "Hard"}
    assert control_request(parse_cli("reboot", "--charger", "charger-a")) == {
        "command": "reboot",
        "charger": "charger-a",
        "type": "Soft",
    }


def test_control_rejects_two_charger_selectors(parse_cli):
    with pytest.raises(ValueError):
        control_request(parse_cli("reboot", "charger-a", "--charger", "charger-b"))


def install_control_response(monkeypatch, response=None, exc=None):
    async def fake_send(data_dir, request):
        assert data_dir
        if exc is not None:
            raise exc
        return response

    monkeypatch.setattr(control_module, "send_control", fake_send)


@pytest.mark.parametrize(("status", "expected_code"), [("Accepted", 0), ("Rejected", 1)])
def test_command_status_controls_exit_code(monkeypatch, parse_cli, status, expected_code):
    install_control_response(monkeypatch, {"ok": True, "response": {"status": status}})
    assert run_control(parse_cli("reboot")) == expected_code


def test_control_error_returns_one(monkeypatch, parse_cli):
    install_control_response(monkeypatch, {"error": "charger_required", "chargers": ["charger-a", "charger-b"]})
    assert run_control(parse_cli("reboot")) == 1


def test_missing_control_socket_returns_one(monkeypatch, parse_cli):
    install_control_response(monkeypatch, exc=FileNotFoundError("control.sock"))
    assert run_control(parse_cli("reboot")) == 1


@pytest.mark.parametrize(
    "args",
    [
        ("start", "--connector", "-1", "--id-tag", "REMOTE"),
        ("start", "--c", "-1", "--id-tag", "REMOTE"),
        ("stop", "--transaction", "-1"),
        ("stop", "--txn", "-1"),
    ],
)
def test_negative_selectors_are_rejected_before_socket_call(parse_cli, args):
    with pytest.raises(ValueError):
        run_control(parse_cli(*args))
