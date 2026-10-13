from ocpp_csms.cli.chargers import run_chargers


def test_cp_is_canonical_singular(parse_cli):
    args = parse_cli("cp", "charger-a")
    assert args.command == "cp"
    assert args.charger == "charger-a"


def test_cps_is_canonical_plural(parse_cli):
    args = parse_cli("cps", "--charging")
    assert args.command == "cps"
    assert args.charging is True


def test_singular_charger_renders_connector_detail(parse_cli, tmp_path, capsys):
    from ocpp_csms.evidence.store import EventStore

    store = EventStore(tmp_path)
    store.record_runtime("charger_connected", charger_id="charger-a", details={"subprotocol": "ocpp1.6"})
    payload = {"connector_id": 1, "status": "Available", "error_code": "NoError"}
    store.record_ocpp("charger-a", "StatusNotification", payload)
    store.record_connector_status("charger-a", payload)
    args = parse_cli("--data-dir", str(tmp_path), "cp", "charger-a")
    assert run_chargers(args) == 0
    output = capsys.readouterr().out
    assert "charger-a" in output
    assert "Connector 1: Available" in output


def test_plural_chargers_renders_fleet_without_appliance_header(parse_cli, tmp_path, capsys):
    from ocpp_csms.evidence.store import EventStore

    store = EventStore(tmp_path)
    store.record_runtime("charger_connected", charger_id="charger-a")
    args = parse_cli("--data-dir", str(tmp_path), "cps")
    assert run_chargers(args) == 0
    output = capsys.readouterr().out
    assert "charger-a" in output
    assert "Connected" in output
    assert "CSMS:" not in output


def test_retired_charge_point_spellings_rejected(parse_cli):
    import pytest

    for name in ("charger", "chargers"):
        with pytest.raises(SystemExit) as error:
            parse_cli(name)
        assert error.value.code == 2


def test_main_dispatches_cp_and_cps_without_aliases(monkeypatch, capsys):
    import sys
    from ocpp_csms.cli import main

    seen = []
    monkeypatch.setattr("ocpp_csms.cli.run_chargers", lambda args: seen.append((args.command, getattr(args, "charger", None))) or 0)
    for argv in (["cp", "charger-a"], ["cps", "--charging"]):
        monkeypatch.setattr(sys, "argv", ["ocpp-csms", *argv])
        assert main() == 0
    assert seen == [("cp", "charger-a"), ("cps", None)]


def test_cp_and_cps_json_contracts(parse_cli, tmp_path, capsys):
    import json

    for argv in (("cp", "charger-a", "-j"), ("cps", "-j")):
        args = parse_cli("--data-dir", str(tmp_path), *argv)
        assert run_chargers(args) == 0
        payload = json.loads(capsys.readouterr().out)
        assert isinstance(payload, dict)
