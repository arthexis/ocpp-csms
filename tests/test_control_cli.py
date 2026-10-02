import pytest

import ocpp_csms.app as app_module
from ocpp_csms.app import build_parser, control_request, run_control


def parse(*args):
    parser, _ = build_parser()
    return parser.parse_args(list(args))


def test_start_command_builds_control_request():
    args = parse("start", "charger-a", "--connector", "2", "--id-tag", "REMOTE")

    assert control_request(args) == {
        "command": "start",
        "charger": "charger-a",
        "id_tag": "REMOTE",
        "connector": 2,
    }


def test_stop_command_builds_control_request():
    args = parse("stop", "charger-a", "--transaction", "42")

    assert control_request(args) == {
        "command": "stop",
        "charger": "charger-a",
        "transaction": 42,
    }


def test_reboot_defaults_to_soft_and_supports_hard():
    assert control_request(parse("reboot", "charger-a")) == {
        "command": "reboot",
        "charger": "charger-a",
        "type": "Soft",
    }
    assert control_request(parse("reboot", "charger-a", "--hard")) == {
        "command": "reboot",
        "charger": "charger-a",
        "type": "Hard",
    }


def test_control_commands_are_available_in_help_topics():
    parser, _ = build_parser()

    for command in ("start", "stop", "reboot"):
        args = parser.parse_args(["help", command])
        assert args.command == "help"
        assert args.topic == command


def install_control_response(monkeypatch, response=None, exc=None):
    async def fake_send(data_dir, request):
        assert data_dir
        if exc is not None:
            raise exc
        return response

    monkeypatch.setattr(app_module, "send_control", fake_send)


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [("Accepted", 0), ("Rejected", 1)],
)
def test_command_status_controls_exit_code(monkeypatch, capsys, status, expected_code):
    install_control_response(
        monkeypatch,
        {"ok": True, "response": {"status": status}},
    )

    result = run_control(parse("reboot", "charger-a"))

    assert result == expected_code
    assert capsys.readouterr().out == f"{status}\n"


def test_control_error_returns_one(monkeypatch, capsys):
    install_control_response(
        monkeypatch,
        {"error": "charger_not_connected", "charger": "charger-a"},
    )

    result = run_control(parse("reboot", "charger-a"))

    assert result == 1
    assert capsys.readouterr().out == "error: charger_not_connected: charger-a\n"


def test_missing_control_socket_returns_one(monkeypatch, capsys):
    install_control_response(monkeypatch, exc=FileNotFoundError("control.sock"))

    result = run_control(parse("reboot", "charger-a"))

    assert result == 1
    assert capsys.readouterr().out == "error: control unavailable: control.sock\n"


@pytest.mark.parametrize(
    "args",
    [
        ("start", "charger-a", "--connector", "-1", "--id-tag", "REMOTE"),
        ("stop", "charger-a", "--transaction", "-1"),
    ],
)
def test_negative_selectors_are_rejected_before_socket_call(args):
    with pytest.raises(ValueError):
        run_control(parse(*args))
