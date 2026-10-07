import pytest

import ocpp_csms.cli.rfid as rfid_module
from ocpp_csms.cli import build_parser
from ocpp_csms.cli.rfid import run_rfid_action


def test_parser_accepts_rfid_local_list_commands():
    parser, _ = build_parser()

    export = parser.parse_args(["rfid", "export", "charger-a"])
    version = parser.parse_args(["rfid", "version"])
    clear = parser.parse_args(["rfid", "clear", "--charger", "charger-a"])

    assert export.rfid_command == "export"
    assert export.charger == "charger-a"
    assert version.rfid_command == "version"
    assert version.charger is None
    assert clear.rfid_command == "clear"
    assert clear.charger_option == "charger-a"


def test_export_requires_valid_rfid_file(tmp_path):
    parsed = build_parser()[0].parse_args(
        ["--data-dir", str(tmp_path), "rfid", "export"]
    )

    with pytest.raises(ValueError, match="rfid.csv is required"):
        rfid_module._control_request(parsed)

    (tmp_path / "rfid.csv").write_text("rfid,enabled\nCARD-A,maybe\n", encoding="utf-8")
    with pytest.raises(ValueError, match="rfid.csv is invalid"):
        rfid_module._control_request(parsed)


def test_export_sends_only_enabled_cards(monkeypatch, tmp_path, capsys):
    (tmp_path / "rfid.csv").write_text(
        "rfid,name,enabled\nCARD-A,Alice,true\nCARD-B,Former,false\n",
        encoding="utf-8",
    )
    parsed = build_parser()[0].parse_args(
        ["--data-dir", str(tmp_path), "rfid", "export", "charger-a"]
    )
    captured = {}

    async def fake_send(data_dir, request):
        captured.update(request)
        return {
            "ok": True,
            "response": {
                "status": "Accepted",
                "charger": "charger-a",
                "previous_version": 2,
                "list_version": 3,
                "cards": 1,
                "verified_version": 3,
                "configuration": {"configuration_key": [{"key": "LocalAuthListEnabled", "readonly": False, "value": "false"}, {"key": "AuthorizationCacheEnabled", "readonly": False, "value": "true"}], "unknown_key": ["LocalPreAuthorize"]},
            },
        }

    monkeypatch.setattr(rfid_module, "send_control", fake_send)

    assert run_rfid_action(parsed) == 0
    assert captured["command"] == "rfid_export"
    assert captured["charger"] == "charger-a"
    assert captured["source_file"] == "rfid.csv"
    assert captured["entries"] == [
        {"rfid": "CARD-A", "name": "Alice", "enabled": True}
    ]
    assert isinstance(captured["list_hash"], str)
    assert len(captured["list_hash"]) == 64
    output = capsys.readouterr().out
    assert "Cards:            1" in output
    assert "Verified:         3" in output
    assert "Charger local list:   false" in output
    assert "Auth cache:           true" in output
    assert "Local preauth:         unknown" in output
    assert "Warning: charger local list is disabled" in output


def test_export_returns_failure_when_charger_rejects(monkeypatch, tmp_path):
    (tmp_path / "rfid.csv").write_text("CARD-A\n", encoding="utf-8")
    parsed = build_parser()[0].parse_args(
        ["--data-dir", str(tmp_path), "rfid", "export"]
    )

    async def fake_send(data_dir, request):
        return {
            "ok": True,
            "response": {
                "status": "Failed",
                "charger": "charger-a",
                "previous_version": 2,
                "list_version": 3,
                "cards": 1,
                "verified_version": None,
            },
        }

    monkeypatch.setattr(rfid_module, "send_control", fake_send)

    assert run_rfid_action(parsed) == 1


def test_version_and_clear_use_control_socket(monkeypatch, tmp_path, capsys):
    parser, _ = build_parser()
    requests = []

    async def fake_send(data_dir, request):
        requests.append(request)
        if request["command"] == "rfid_version":
            return {"ok": True, "response": {"list_version": 7, "configuration": {"configuration_key": [], "unknown_key": []}}}
        return {
            "ok": True,
            "response": {
                "status": "Accepted",
                "charger": "charger-a",
                "previous_version": 7,
                "list_version": 0,
                "cards": 0,
                "verified_version": 0,
            },
        }

    monkeypatch.setattr(rfid_module, "send_control", fake_send)

    version = parser.parse_args(["--data-dir", str(tmp_path), "rfid", "version"])
    clear = parser.parse_args(["--data-dir", str(tmp_path), "rfid", "clear"])

    assert run_rfid_action(version) == 0
    assert "RFID charger local list version: 7" in capsys.readouterr().out
    assert run_rfid_action(clear) == 0
    assert requests == [{"command": "rfid_version"}, {"command": "rfid_clear"}]


