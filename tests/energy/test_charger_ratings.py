"""Nominal ratings are operational metadata; never OCPP control or measured power."""
import json

import pytest

from ocpp_csms.charger_ratings import load_ratings, rating_for
from ocpp_csms.cli import build_parser
from ocpp_csms.cli.rating import run_rating
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.export_contract import export_contract


def invoke(tmp_path, *parts):
    parser = build_parser()[0]
    return run_rating(parser.parse_args(["--data-dir", str(tmp_path), "rating", *parts]))


def test_default_cp_and_all_observed_physical_connectors(tmp_path, capsys):
    store = EventStore(tmp_path)
    for ident in (0, 1, 2):
        store.record_connector_status("single-cp", {
            "connector_id": ident, "status": "Available", "error_code": "NoError"
        })
    assert invoke(tmp_path, "set", "--power-kw", "50") == 0  # one known CP
    assert invoke(tmp_path, "set", "--connector", "all", "--power-kw", "25") == 0
    ratings = load_ratings(tmp_path)
    assert ratings["single-cp"] == {
        "default_w": 50000, "connectors": {"1": 25000, "2": 25000}
    }
    assert rating_for(ratings, "single-cp", 0) == (50000, "charger")
    assert rating_for(ratings, "single-cp", 1) == (25000, "connector")
    assert rating_for(ratings, "single-cp", 2) == (25000, "connector")
    assert invoke(tmp_path, "clear", "--connector", "1") == 0
    assert rating_for(load_ratings(tmp_path), "single-cp", 1) == (50000, "charger")
    assert invoke(tmp_path, "show", "--json") == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["single-cp"]["default_w"] == 50000


def test_ratings_export_even_when_no_new_events(tmp_path):
    store = EventStore(tmp_path)
    for ident in (0, 1, 2):
        store.record_connector_status("charger-a", {
            "connector_id": ident, "status": "Available", "error_code": "NoError"
        })
    assert invoke(tmp_path, "set", "--cp", "charger-a", "--power-kw", "50") == 0
    assert invoke(tmp_path, "set", "--cp", "charger-a", "--connector", "1", "--power-kw", "22") == 0
    page = export_contract(tmp_path, after=0)["data"]
    charger = next(c for c in page["status"]["chargers"] if c["id"] == "charger-a")
    by_id = {c["id"]: c for c in charger["connectors"]}
    assert charger["rated_power_w"] == 50000
    assert by_id[0]["rated_power_w"] == 50000
    assert by_id[1]["rated_power_w"] == 22000
    assert by_id[1]["rating_scope"] == "connector"
    assert by_id[2]["rated_power_w"] == 50000
    cursor = page["cursor"]["next"]
    assert invoke(tmp_path, "clear", "--cp", "charger-a", "--connector", "1") == 0
    empty = export_contract(tmp_path, after=cursor)["data"]
    assert empty["events"] == []
    by_id = {c["id"]: c for c in empty["status"]["chargers"][0]["connectors"]}
    assert by_id[1]["rating_scope"] == "charger"


def test_reject_ambiguous_or_invalid_ratings(tmp_path):
    store = EventStore(tmp_path)
    store.record_connector_status("a", {"connector_id": 1, "status": "Available", "error_code": "NoError"})
    store.record_connector_status("b", {"connector_id": 1, "status": "Available", "error_code": "NoError"})
    with pytest.raises(ValueError, match="Specify --cp"):
        invoke(tmp_path, "set", "--power-kw", "7.2")
    with pytest.raises(ValueError, match="positive"):
        invoke(tmp_path, "set", "--cp", "a", "--power-kw", "-1")
    with pytest.raises(ValueError, match="connector 0"):
        invoke(tmp_path, "set", "--cp", "a", "--connector", "0", "--power-kw", "22")
    with pytest.raises(ValueError, match="physical"):
        invoke(tmp_path, "set", "--cp", "unknown", "--connector", "all", "--power-kw", "22")
    assert not (tmp_path / "charger-ratings.json").exists()
