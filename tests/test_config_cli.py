import pytest

import ocpp_csms.cli.config as config_module
from ocpp_csms.cli import build_parser
from ocpp_csms.cli.config import configuration_request, run_configuration


def parse(*args):
    parser, _ = build_parser()
    return parser.parse_args(list(args))


@pytest.mark.parametrize(
    ("argv", "expected_charger", "expected_keys", "expected_force"),
    [
        (("config",), None, None, False),
        (("config", "HeartbeatInterval", "GetConfigurationMaxKeys"), None, ["HeartbeatInterval", "GetConfigurationMaxKeys"], False),
        (("config", "-f"), None, None, True),
        (("config", "--charger", "charger-a"), "charger-a", None, False),
        (("config", "--charger", "charger-a", "HeartbeatInterval"), "charger-a", ["HeartbeatInterval"], False),
        (("config", "HeartbeatInterval", "--force"), None, ["HeartbeatInterval"], True),
    ],
)
def test_config_request_preserves_selector_keys_and_force(argv, expected_charger, expected_keys, expected_force):
    request = configuration_request(parse(*argv))
    assert request["command"] == "config"
    assert request.get("charger") == expected_charger
    assert request["force"] is expected_force
    assert request.get("keys") == expected_keys


def test_config_set_builds_change_request():
    assert configuration_request(parse("config", "set", "HeartbeatInterval", "60")) == {
        "command": "config_set",
        "key": "HeartbeatInterval",
        "value": "60",
        "force": False,
    }


def test_legacy_config_positional_charger_remains_a_key():
    request = configuration_request(parse("config", "charger-a", "HeartbeatInterval"))
    assert "charger" not in request
    assert request["keys"] == ["charger-a", "HeartbeatInterval"]


def install_response(monkeypatch, response):
    async def fake_send(data_dir, request):
        assert data_dir
        return response

    monkeypatch.setattr(config_module, "send_control", fake_send)


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        ({"ok": True, "response": {"configuration_key": [{"key": "HeartbeatInterval", "readonly": False, "value": "300"}], "unknown_key": ["VendorThing"]}}, 0),
        ({"error": "active_transaction", "charger": "charger-a", "transactions": [17]}, 1),
        ({"ok": True, "response": {"configuration_key": "bad"}}, 1),
    ],
)
def test_configuration_result_controls_exit_code(monkeypatch, response, expected_code):
    install_response(monkeypatch, response)
    assert run_configuration(parse("config", "HeartbeatInterval")) == expected_code
