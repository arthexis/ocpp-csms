from __future__ import annotations

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.export_contract import export_contract


def test_cursor_after_is_exclusive_and_next_is_last_included(tmp_path):
    store = EventStore(tmp_path)
    for index in range(4):
        store.record_ocpp("charger-a", "Authorize", {"id_tag": str(index)})
    first = export_contract(tmp_path, after=1, limit=2)["data"]
    assert [event["id"] for event in first["events"]] == [2, 3]
    assert first["cursor"] == {"after": 1, "next": 3, "more": True}
    last = export_contract(tmp_path, after=first["cursor"]["next"], limit=2)["data"]
    assert [event["id"] for event in last["events"]] == [4]
    assert last["cursor"] == {"after": 3, "next": 4, "more": False}


def test_empty_page_retains_supplied_cursor(tmp_path):
    EventStore(tmp_path)
    page = export_contract(tmp_path, after=42, limit=1)["data"]
    assert page["cursor"] == {"after": 42, "next": 42, "more": False}
    assert page["events"] == []


def test_cursor_rejects_negative_after_and_nonpositive_limit(tmp_path):
    EventStore(tmp_path)
    for kwargs in ({"after": -1}, {"limit": 0}):
        try:
            export_contract(tmp_path, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid cursor parameters accepted: {kwargs}")
