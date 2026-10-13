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
