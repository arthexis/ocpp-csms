import pytest

import ocpp_csms.app as app_module
from ocpp_csms.app import build_parser, configuration_request, control_request, run_configuration, run_control


def parse(*args):
    parser, _ = build_parser()
    return parser.parse_args(list(args))


def test_start_command_builds_control_request():
    expected = {
        "command": "start",
        "charger": "charger-a",
        "id_tag": "REMOTE",
        "connector": 2,
    }

    assert control_request(parse("start", "charger-a", "--connector", "2", "--id-tag", "REMOTE")) == expected
    assert control_request(parse("start", "charger-a", "--cp", "2", "--id-tag", "REMOTE")) == expected


def test_stop_command_builds_control_request():
    expected = {
        "command": "stop",
        "charger": "charger-a",
        "transaction": 42,
    }

    assert control_request(parse("stop", "charger-a", "--transaction", "42")) == expected
    assert control_request(parse("stop", "charger-a", "--txn", "42")) == expected


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


@pytest.mark.parametrize(
    ("argv", "expected_keys", "expected_force"),
    [
        (("config", "charger-a"), None, False),
        (("config", "charger-a", "HeartbeatInterval", "GetConfigurationMaxKeys"), ["HeartbeatInterval", "GetConfigurationMaxKeys"], False),
        (("config", "charger-a", "-f"), None, True),
        (("config", "-f", "charger-a"), None, True),
        (("config", "charger-a", "HeartbeatInterval", "--force"), ["HeartbeatInterval"], True),
        (("config", "--force", "charger-a", "HeartbeatInterval"), ["HeartbeatInterval"], True),
    ],
)
def test_config_request_preserves_keys_and_force_regardless_of_option_position(argv, expected_keys, expected_force):
    request = configuration_request(parse(*argv))

    assert request["command"] == "config"
    assert request["charger"] == "charger-a"
    assert request["force"] is expected_force
    assert request.get("keys") == expected_keys


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
def test_command_status_controls_exit_code(monkeypatch, status, expected_code):
    install_control_response(
        monkeypatch,
        {"ok": True, "response": {"status": status}},
    )

    assert run_control(parse("reboot", "charger-a")) == expected_code


def test_control_error_returns_one(monkeypatch):
    install_control_response(
        monkeypatch,
        {"error": "charger_not_connected", "charger": "charger-a"},
    )

    assert run_control(parse("reboot", "charger-a")) == 1


def test_missing_control_socket_returns_one(monkeypatch):
    install_control_response(monkeypatch, exc=FileNotFoundError("control.sock"))

    assert run_control(parse("reboot", "charger-a")) == 1


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (
            {
                "ok": True,
                "response": {
                    "configuration_key": [
                        {"key": "HeartbeatInterval", "readonly": False, "value": "300"}
                    ],
                    "unknown_key": ["VendorThing"],
                },
            },
            0,
        ),
        ({"error": "active_transaction", "charger": "charger-a", "transactions": [17]}, 1),
        ({"ok": True, "response": {"configuration_key": "bad"}}, 1),
    ],
)
def test_configuration_result_controls_exit_code(monkeypatch, response, expected_code):
    install_control_response(monkeypatch, response)

    assert run_configuration(parse("config", "charger-a")) == expected_code


@pytest.mark.parametrize(
    "args",
    [
        ("start", "charger-a", "--connector", "-1", "--id-tag", "REMOTE"),
        ("start", "charger-a", "--cp", "-1", "--id-tag", "REMOTE"),
        ("stop", "charger-a", "--transaction", "-1"),
        ("stop", "charger-a", "--txn", "-1"),
    ],
)
def test_negative_selectors_are_rejected_before_socket_call(args):
    with pytest.raises(ValueError):
        run_control(parse(*args))
