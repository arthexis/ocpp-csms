import pytest

import ocpp_csms.cli.config as config_module
from ocpp_csms.cli.config import configuration_request, run_configuration


@pytest.mark.parametrize(
    ("argv", "expected_charger", "expected_keys", "expected_force"),
    [
        (("config",), None, None, False),
        (("config", "HeartbeatInterval", "GetConfigurationMaxKeys"), None, ["HeartbeatInterval", "GetConfigurationMaxKeys"], False),
        (("config", "-f"), None, None, True),
        (("config", "--cp", "charger-a"), "charger-a", None, False),
        (("config", "--cp", "charger-a", "HeartbeatInterval"), "charger-a", ["HeartbeatInterval"], False),
        (("config", "HeartbeatInterval", "--force"), None, ["HeartbeatInterval"], True),
    ],
)
def test_config_request_preserves_selector_keys_and_force(parse_cli, argv, expected_charger, expected_keys, expected_force):
    request = configuration_request(parse_cli(*argv))
    assert request["command"] == "config"
    assert request.get("charger") == expected_charger
    assert request["force"] is expected_force
    assert request.get("keys") == expected_keys


def test_legacy_config_positional_charger_remains_a_key(parse_cli):
    request = configuration_request(parse_cli("config", "charger-a", "HeartbeatInterval"))
    assert "charger" not in request
    assert request["keys"] == ["charger-a", "HeartbeatInterval"]


def test_config_set_builds_change_request(parse_cli):
    assert configuration_request(parse_cli("config", "set", "HeartbeatInterval", "60")) == {
        "command": "config_set",
        "key": "HeartbeatInterval",
        "value": "60",
        "force": False,
    }


def test_config_set_supports_explicit_charger_and_force(parse_cli):
    assert configuration_request(
        parse_cli("config", "--cp", "charger-a", "set", "HeartbeatInterval", "60", "--force")
    ) == {
        "command": "config_set",
        "key": "HeartbeatInterval",
        "value": "60",
        "force": True,
        "charger": "charger-a",
    }


@pytest.mark.parametrize("argv", [("config", "set"), ("config", "set", "HeartbeatInterval")])
def test_config_set_requires_key_and_value(parse_cli, argv):
    with pytest.raises(ValueError):
        configuration_request(parse_cli(*argv))


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
def test_configuration_result_controls_exit_code(monkeypatch, parse_cli, response, expected_code):
    install_response(monkeypatch, response)
    assert run_configuration(parse_cli("config", "HeartbeatInterval")) == expected_code


def test_config_set_prints_readback_and_accepts_reboot_required(monkeypatch, parse_cli, capsys):
    install_response(
        monkeypatch,
        {
            "ok": True,
            "response": {
                "change": {"status": "RebootRequired"},
                "readback": {
                    "configuration_key": [{"key": "HeartbeatInterval", "readonly": False, "value": "60"}],
                    "unknown_key": [],
                },
            },
        },
    )
    assert run_configuration(parse_cli("config", "set", "HeartbeatInterval", "60")) == 0
    output = capsys.readouterr().out
    assert "RebootRequired" in output
    assert "HeartbeatInterval" in output
    assert "60" in output
