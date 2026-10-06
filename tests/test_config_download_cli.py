import json

import pytest

import ocpp_csms.cli as cli


def parse_download(*args):
    parser = cli._build_download_parser()
    return parser.parse_args(["config", "download", *args])


def install_control_response(monkeypatch, response):
    async def fake_send(data_dir, request):
        assert data_dir
        install_control_response.request = request
        return response

    monkeypatch.setattr(cli, "send_control", fake_send)


def test_download_uses_full_guarded_configuration_query(monkeypatch):
    install_control_response(
        monkeypatch,
        {
            "ok": True,
            "response": {
                "configuration_key": [
                    {"key": "HeartbeatInterval", "readonly": False, "value": "60"}
                ],
                "unknown_key": [],
            },
        },
    )
    monkeypatch.setattr(cli, "_connected_charger", lambda data_dir: "charger-a")

    assert cli.run_config_download(parse_download()) == 0
    assert install_control_response.request == {"command": "config", "force": False}


def test_download_forwards_explicit_charger_and_force(monkeypatch):
    install_control_response(
        monkeypatch,
        {"ok": True, "response": {"configuration_key": [], "unknown_key": []}},
    )

    assert cli.run_config_download(parse_download("charger-a", "--force")) == 0
    assert install_control_response.request == {
        "command": "config",
        "force": True,
        "charger": "charger-a",
    }


def test_download_reports_active_transaction_guard(monkeypatch, capsys):
    install_control_response(
        monkeypatch,
        {"error": "active_transaction", "charger": "charger-a", "transactions": [17]},
    )

    assert cli.run_config_download(parse_download("charger-a")) == 1
    assert "active_transaction" in capsys.readouterr().out


def test_download_writes_masked_json_snapshot(monkeypatch, tmp_path):
    install_control_response(
        monkeypatch,
        {
            "ok": True,
            "response": {
                "configuration_key": [
                    {"key": "AuthorizationKey", "readonly": True, "value": "secret"}
                ],
                "unknown_key": [],
            },
        },
    )
    output = tmp_path / "configuration.json"

    assert cli.run_config_download(parse_download("charger-a", "--output", str(output))) == 0
    snapshot = json.loads(output.read_text())
    assert snapshot["charger"] == "charger-a"
    assert snapshot["configuration"][0] == {
        "key": "AuthorizationKey",
        "readonly": True,
        "value": "[REDACTED]",
    }


def test_download_show_sensitive_is_explicit(monkeypatch, tmp_path):
    install_control_response(
        monkeypatch,
        {
            "ok": True,
            "response": {
                "configuration_key": [
                    {"key": "AuthorizationKey", "readonly": False, "value": "secret"}
                ],
                "unknown_key": [],
            },
        },
    )
    output = tmp_path / "configuration.json"

    assert cli.run_config_download(
        parse_download("charger-a", "--show-sensitive", "--output", str(output))
    ) == 0
    assert json.loads(output.read_text())["configuration"][0]["value"] == "secret"


def test_download_rejects_duplicate_charger_selectors():
    assert cli.run_config_download(parse_download("charger-a", "--charger", "charger-b")) == 2
