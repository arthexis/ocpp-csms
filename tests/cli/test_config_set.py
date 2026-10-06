import pytest

import ocpp_csms.app as app_module
from ocpp_csms.app import build_parser, configuration_request, run_configuration


def parse(*args):
    parser, _ = build_parser()
    return parser.parse_args(list(args))


def test_config_set_builds_change_request_with_inferred_charger():
    assert configuration_request(parse("config", "set", "HeartbeatInterval", "60")) == {
        "command": "config_set",
        "key": "HeartbeatInterval",
        "value": "60",
        "force": False,
    }


def test_config_set_supports_explicit_charger_and_force():
    assert configuration_request(
        parse("config", "--charger", "charger-a", "set", "HeartbeatInterval", "60", "--force")
    ) == {
        "command": "config_set",
        "key": "HeartbeatInterval",
        "value": "60",
        "force": True,
        "charger": "charger-a",
    }


@pytest.mark.parametrize("argv", [("config", "set"), ("config", "set", "HeartbeatInterval")])
def test_config_set_requires_key_and_value(argv):
    with pytest.raises(ValueError):
        configuration_request(parse(*argv))


def test_config_set_prints_readback_and_accepts_reboot_required(monkeypatch, capsys):
    async def fake_send(data_dir, request):
        return {
            "ok": True,
            "response": {
                "change": {"status": "RebootRequired"},
                "readback": {
                    "configuration_key": [
                        {"key": "HeartbeatInterval", "readonly": False, "value": "60"}
                    ],
                    "unknown_key": [],
                },
            },
        }

    monkeypatch.setattr(app_module, "send_control", fake_send)
    assert run_configuration(parse("config", "set", "HeartbeatInterval", "60")) == 0
    output = capsys.readouterr().out
    assert "RebootRequired" in output
    assert "HeartbeatInterval" in output
    assert "60" in output
