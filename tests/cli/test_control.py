import pytest

import ocpp_csms.cli.control as control_module
from ocpp_csms.cli.control import control_request, run_control


def test_start_command_builds_control_request_with_or_without_charger(parse_cli):
    assert control_request(parse_cli("start", "--connector", "2", "--id-tag", "REMOTE", "--now")) == {
        "command": "start",
        "timing": "now",
        "id_tag": "REMOTE",
        "connector": 2,
    }

    expected = {
        "command": "start",
        "timing": "now",
        "charger": "charger-a",
        "id_tag": "REMOTE",
        "connector": 2,
    }
    assert control_request(parse_cli("start", "charger-a", "--connector", "2", "--id-tag", "REMOTE", "--now")) == expected
    assert control_request(parse_cli("start", "--cp", "charger-a", "-c", "2", "--id-tag", "REMOTE", "--now")) == expected


def test_stop_command_builds_control_request_with_or_without_charger(parse_cli):
    assert control_request(parse_cli("stop", "--transaction", "42", "--now")) == {
        "command": "stop",
        "timing": "now",
        "transaction": 42,
    }

    expected = {
        "command": "stop",
        "timing": "now",
        "charger": "charger-a",
        "transaction": 42,
    }
    assert control_request(parse_cli("stop", "charger-a", "--transaction", "42", "--now")) == expected
    assert control_request(parse_cli("stop", "--cp", "charger-a", "--txn", "42", "--now")) == expected


def test_reset_defaults_to_soft_and_supports_explicit_charger(parse_cli):
    assert control_request(parse_cli("reset", "--now")) == {"command": "reset", "timing": "now", "type": "Soft"}
    assert control_request(parse_cli("reset", "--hard", "--now")) == {"command": "reset", "timing": "now", "type": "Hard"}
    assert control_request(parse_cli("reset", "--cp", "charger-a", "--now")) == {
        "command": "reset",
        "timing": "now",
        "charger": "charger-a",
        "type": "Soft",
    }


def test_control_rejects_two_charger_selectors(parse_cli):
    with pytest.raises(ValueError):
        control_request(parse_cli("reset", "charger-a", "--cp", "charger-b", "--now"))


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
    assert run_control(parse_cli("reset", "--now")) == expected_code


def test_control_error_returns_one(monkeypatch, parse_cli):
    install_control_response(monkeypatch, {"error": "charger_required", "chargers": ["charger-a", "charger-b"]})
    assert run_control(parse_cli("reset", "--now")) == 1


def test_missing_control_socket_returns_one(monkeypatch, parse_cli):
    install_control_response(monkeypatch, exc=FileNotFoundError("control.sock"))
    assert run_control(parse_cli("reset", "--now")) == 1


@pytest.mark.parametrize(
    "args",
    [
        ("start", "--connector", "-1", "--id-tag", "REMOTE", "--now"),
        ("start", "-c", "-1", "--id-tag", "REMOTE", "--now"),
        ("stop", "--transaction", "-1", "--now"),
        ("stop", "--txn", "-1", "--now"),
    ],
)
def test_negative_selectors_are_rejected_before_socket_call(parse_cli, args):
    with pytest.raises(ValueError):
        run_control(parse_cli(*args))


def test_control_commands_require_exactly_one_timing_mode(cli_parser):
    for args in (
        ["start", "--id-tag", "REMOTE"],
        ["stop", "--transaction", "42"],
        ["reset"],
    ):
        with pytest.raises(SystemExit):
            cli_parser.parse_args(args)

    with pytest.raises(SystemExit):
        cli_parser.parse_args(["reset", "--now", "--after", "5"])


@pytest.mark.parametrize(
    ("args", "timing"),
    [
        (("reset", "--now"), {"timing": "now"}),
        (("reset", "--after", "5"), {"timing": "after", "seconds": 5}),
        (("reset", "--within", "30"), {"timing": "within", "seconds": 30}),
    ],
)
def test_timing_mode_is_sent_to_control_server(parse_cli, args, timing):
    request = control_request(parse_cli(*args))
    assert {key: request[key] for key in timing} == timing


@pytest.mark.parametrize(
    "args",
    [
        ("reset", "--after", "0"),
        ("reset", "--within", "0"),
        ("reset", "--after", "-1"),
        ("reset", "--within", "-1"),
    ],
)
def test_delayed_timing_requires_positive_seconds(parse_cli, args):
    with pytest.raises(ValueError, match="greater than zero"):
        run_control(parse_cli(*args))


def test_remote_start_uses_cp_for_charge_point(cli_parser):
    with pytest.raises(SystemExit):
        cli_parser.parse_args(["start", "--cp", "2", "--id-tag", "REMOTE", "--now"])
