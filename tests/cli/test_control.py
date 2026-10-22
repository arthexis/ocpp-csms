import pytest

import ocpp_csms.cli.control as control_module
from ocpp_csms.cli.control import control_request, run_control
from ocpp_csms.cli.transactions import build_txn_operations_parser


@pytest.fixture
def parse_cli(parse_cli):
    """Keep control request assertions on the canonical transaction parser."""
    def parse(*argv):
        if argv[0] in {"start", "stop"}:
            args = build_txn_operations_parser().parse_args(argv)
            args.command = args.operation
            if args.operation == "stop":
                args.transaction = args.transaction_id
                args.charger = None
            return args
        return parse_cli(*argv)
    return parse



def test_start_command_builds_control_request_with_or_without_charger(parse_cli):
    assert control_request(parse_cli("start", "--connector", "2", "--rfid", "REMOTE")) == {
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
    assert control_request(parse_cli("start", "--cp", "charger-a", "--connector", "2", "--rfid", "REMOTE")) == expected
    assert control_request(parse_cli("start", "--cp", "charger-a", "-c", "2", "--rfid", "REMOTE")) == expected


def test_stop_command_builds_control_request_with_or_without_charger(parse_cli):
    assert control_request(parse_cli("stop", "42")) == {
        "command": "stop",
        
        "transaction": 42,
    }

    expected = {
        "command": "stop",
        
        "charger": "charger-a",
        "transaction": 42,
    }
    assert control_request(parse_cli("stop", "42", "--cp", "charger-a")) == expected


def test_reset_defaults_to_soft_and_supports_explicit_charger(parse_cli):
    assert control_request(parse_cli("reset")) == {"command": "reset", "type": "Soft"}
    assert control_request(parse_cli("reset", "--hard")) == {"command": "reset", "type": "Hard"}
    assert control_request(parse_cli("reset", "--cp", "charger-a")) == {
        "command": "reset",
        
        "charger": "charger-a",
        "type": "Soft",
    }


def test_control_rejects_positional_charger(parse_cli):
    with pytest.raises(SystemExit):
        parse_cli("reset", "charger-a")


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
    assert run_control(parse_cli("reset")) == expected_code


def test_control_error_returns_one(monkeypatch, parse_cli):
    install_control_response(monkeypatch, {"error": "charger_required", "chargers": ["charger-a", "charger-b"]})
    assert run_control(parse_cli("reset")) == 1


def test_missing_control_socket_returns_one(monkeypatch, parse_cli):
    install_control_response(monkeypatch, exc=FileNotFoundError("control.sock"))
    assert run_control(parse_cli("reset")) == 1


@pytest.mark.parametrize(
    "args",
    [
        ("start", "--connector", "-1", "--rfid", "REMOTE"),
        ("start", "-c", "-1", "--rfid", "REMOTE"),
        ("stop", "-1"),
    ],
)
def test_negative_selectors_are_rejected_before_socket_call(parse_cli, args):
    with pytest.raises(ValueError):
        run_control(parse_cli(*args))


def test_start_optional_rfid(parse_cli):
    assert control_request(parse_cli("start"))["id_tag"] == "AUTO"
    assert control_request(parse_cli("start", "--rfid", "TEST001"))["id_tag"] == "TEST001"


def test_remote_start_uses_cp_for_charge_point(parse_cli):
    args = parse_cli("start", "--cp", "charger-a", "-c", "2", "--rfid", "REMOTE")
    assert args.charger_option == "charger-a"
    assert args.connector == 2
