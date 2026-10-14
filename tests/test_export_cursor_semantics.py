from __future__ import annotations

import pytest

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.export_contract import export_contract


def test_export_event_cursor_is_exclusive_and_next_is_inclusive(tmp_path):
    store = EventStore(tmp_path)
    for index in range(4):
        store.record_ocpp("cp-a", "Authorize", {"id_tag": str(index)})
    first = export_contract(tmp_path, after=1, limit=2)
    assert first["schema"] == "ocpp-csms/export/v1"
    data = first["data"]
    assert [event["id"] for event in data["events"]] == [2, 3]
    assert data["cursor"] == {"after": 1, "next": 3, "more": True}
    last = export_contract(tmp_path, after=3, limit=2)["data"]
    assert [event["id"] for event in last["events"]] == [4]
    assert last["cursor"] == {"after": 3, "next": 4, "more": False}


def test_export_empty_page_retains_cursor(tmp_path):
    EventStore(tmp_path)
    page = export_contract(tmp_path, after=42, limit=1)["data"]
    assert page["events"] == []
    assert page["cursor"] == {"after": 42, "next": 42, "more": False}


def test_export_replay_preserves_event_identity(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("cp-a", "Heartbeat", {})
    first = export_contract(tmp_path, after=0, limit=1)["data"]
    replay = export_contract(tmp_path, after=0, limit=1)["data"]
    assert [row["id"] for row in first["events"]] == [row["id"] for row in replay["events"]]
    assert first["source_id"] == replay["source_id"]


@pytest.mark.parametrize("kwargs", [{"after": -1}, {"limit": 0}, {"limit": -1}])
def test_export_rejects_invalid_cursor_parameters(tmp_path, kwargs):
    with pytest.raises(ValueError):
        export_contract(tmp_path, **kwargs)


def test_export_retransmission_events_are_distinct_and_restart_replay_stable(tmp_path):
    store = EventStore(tmp_path)
    payload = {"connector_id": 1, "id_tag": "card-a",
               "meter_start": 100, "timestamp": "2026-10-03T10:00:00Z"}
    for _ in range(2):
        store.record_ocpp("cp-a", "StartTransaction", payload, transaction_id=41)
    original = export_contract(tmp_path, limit=1)["data"]
    assert original["cursor"]["more"] is True
    first = original["events"][0]
    EventStore(tmp_path)
    next_page = export_contract(tmp_path, after=original["cursor"]["next"], limit=1)["data"]
    second = next_page["events"][0]
    assert first["id"] != second["id"]
    assert first["transaction_id"] == second["transaction_id"] == 41
    assert first["payload"] == second["payload"]
    assert export_contract(tmp_path, limit=1)["data"]["events"][0] == first
    assert export_contract(tmp_path, after=first["id"], limit=1)["data"]["events"][0] == second


def test_export_late_stop_is_visible_without_mutating_old_event(tmp_path):
    store = EventStore(tmp_path)
    start = {"connector_id": 1, "id_tag": "card-a",
             "meter_start": 100, "timestamp": "2026-10-03T10:00:00Z"}
    store.record_ocpp("cp-a", "StartTransaction", start, transaction_id=41)
    initial = export_contract(tmp_path)["data"]
    original_event = initial["events"][0]
    stop = {"transaction_id": 41, "meter_stop": 150,
            "timestamp": "2026-10-03T10:10:00Z"}
    store.record_ocpp("cp-a", "StopTransaction", stop)
    later = export_contract(tmp_path, after=initial["cursor"]["next"])["data"]
    assert len(later["events"]) == 1
    assert later["events"][0]["id"] > original_event["id"]
    assert later["events"][0]["action"] == "StopTransaction"
    assert later["events"][0]["payload"] == stop
    assert export_contract(tmp_path)["data"]["events"][0] == original_event


def test_export_cursor_does_not_advance_when_sqlite_insert_fails(tmp_path, monkeypatch):
    import sqlite3

    store = EventStore(tmp_path)
    store.record_ocpp("cp-a", "Heartbeat", {})
    initial = export_contract(tmp_path)["data"]
    cursor = initial["cursor"]["next"]
    original_connect = store._connect

    def failed_connection():
        raise sqlite3.OperationalError("simulated unavailable event store")

    monkeypatch.setattr(store, "_connect", failed_connection)
    with pytest.raises(sqlite3.OperationalError, match="simulated unavailable"):
        store.record_ocpp("cp-a", "StopTransaction", {"transaction_id": 41})
    assert export_contract(tmp_path, after=cursor)["data"]["events"] == []
    monkeypatch.setattr(store, "_connect", original_connect)
    store.record_ocpp("cp-a", "StopTransaction", {"transaction_id": 41})
    page = export_contract(tmp_path, after=cursor)["data"]
    assert len(page["events"]) == 1
    assert page["cursor"]["next"] > cursor
