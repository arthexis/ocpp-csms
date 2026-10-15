"""SQLite-authoritative transaction contract for issues #247 and #244.

No JSON archive is used as a source of transaction identity or export evidence.
"""
import sqlite3

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.export_contract import export_contract


def start():
    return {"connector_id": 1, "id_tag": "card-a", "meter_start": 100,
            "timestamp": "2026-10-03T10:00:00Z"}


def test_sqlite_retry_identity_survives_restart_without_json(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("charger-a", start())
    restarted = EventStore(tmp_path)
    assert restarted.commit_start("charger-a", start()) == transaction_id
    assert not list((tmp_path / "transactions").glob("*/*.json"))


def test_sqlite_export_does_not_require_json_archive(tmp_path):
    store = EventStore(tmp_path)
    payload = start()
    transaction_id = store.commit_start("charger-a", payload)
    events = export_contract(tmp_path, after=0, limit=100)["data"]["events"]
    assert any(event["action"] == "StartTransaction" for event in events)
    assert not list((tmp_path / "transactions").glob("*/*.json"))
    assert export_contract(tmp_path, after=0, limit=100)["data"]["events"] == events


def test_sqlite_exact_retry_lookup_has_no_time_window(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("charger-a", start())
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE transactions SET start_received_at = ? WHERE transaction_id = ?",
            ("2020-01-01T00:00:00Z", transaction_id),
        )
    assert EventStore(tmp_path).commit_start("charger-a", start()) == transaction_id


def test_sqlite_retry_identity_is_charge_point_scoped(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("charger-a", start())
    assert store.commit_start("charger-b", start()) != transaction_id
