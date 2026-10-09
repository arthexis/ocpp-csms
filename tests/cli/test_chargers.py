from ocpp_csms.cli.chargers import run_chargers


def test_charger_and_cp_aliases_normalize_to_singular(parse_cli):
    for name in ("charger", "cp"):
        args = parse_cli(name, "charger-a")
        assert args.command == "charger"
        assert args.charger == "charger-a"


def test_chargers_and_cps_aliases_normalize_to_plural(parse_cli):
    for name in ("chargers", "cps"):
        args = parse_cli(name, "--charging")
        assert args.command == "chargers"
        assert args.charging is True


def test_singular_charger_renders_connector_detail(parse_cli, tmp_path, capsys):
    from ocpp_csms.events import EventStore

    store = EventStore(tmp_path)
    store.record_runtime("charger_connected", charger_id="charger-a", details={"subprotocol": "ocpp1.6"})
    payload = {"connector_id": 1, "status": "Available", "error_code": "NoError"}
    store.record_ocpp("charger-a", "StatusNotification", payload)
    store.record_connector_status("charger-a", payload)
    args = parse_cli("--data-dir", str(tmp_path), "charger", "charger-a")
    assert run_chargers(args) == 0
    output = capsys.readouterr().out
    assert "charger-a" in output
    assert "Connector 1: Available" in output


def test_plural_chargers_renders_fleet_without_appliance_header(parse_cli, tmp_path, capsys):
    from ocpp_csms.events import EventStore

    store = EventStore(tmp_path)
    store.record_runtime("charger_connected", charger_id="charger-a")
    args = parse_cli("--data-dir", str(tmp_path), "chargers")
    assert run_chargers(args) == 0
    output = capsys.readouterr().out
    assert "charger-a" in output
    assert "Connected" in output
    assert "CSMS:" not in output
