from __future__ import annotations

import json

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.export_contract import export_contract


def _transaction(tmp_path, transaction_id: int = 7) -> None:
    directory = tmp_path / "transactions" / "2026-10-07"
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "transaction_id": transaction_id,
        "origin": "local",
        "charge_point_id": "charger-a",
        "status": "open",
        "created_at": "2026-10-07T12:00:00Z",
        "updated_at": "2026-10-07T12:01:00Z",
        "id_tag": "CARD-A",
        "start": {
            "connector_id": 1,
            "id_tag": "CARD-A",
            "meter_start": 1000,
            "timestamp": "2026-10-07T12:00:00Z",
        },
        "start_received_at": "2026-10-07T12:00:00Z",
        "meter_values": [],
        "meter_values_received_at": [],
        "stop": None,
        "stop_received_at": None,
    }
    (directory / f"charger-a-{transaction_id}.json").write_text(json.dumps(record))


def test_empty_export_has_stable_cursor_and_source(tmp_path):
    EventStore(tmp_path)

    payload = export_contract(tmp_path, after=0, limit=10)

    assert payload["schema"] == "ocpp-csms/export/v1"
    data = payload["data"]
    assert data["source_id"]
    assert data["cursor"] == {"after": 0, "next": 0, "more": False}
    assert data["events"] == []
    assert data["energy"] == []
    assert data["transactions"] == []
    assert "chargers" in data["status"]


def test_export_pages_by_ocpp_event_id_and_retry_is_idempotent(tmp_path):
    store = EventStore(tmp_path)
    for index in range(3):
        store.record_ocpp("charger-a", "Authorize", {"id_tag": f"CARD-{index}"})

    first = export_contract(tmp_path, after=0, limit=2)
    retry = export_contract(tmp_path, after=0, limit=2)
    second = export_contract(tmp_path, after=2, limit=2)

    assert [event["id"] for event in first["data"]["events"]] == [1, 2]
    assert first["data"]["cursor"] == {"after": 0, "next": 2, "more": True}
    assert retry["data"]["events"] == first["data"]["events"]
    assert retry["data"]["cursor"] == first["data"]["cursor"]
    assert [event["id"] for event in second["data"]["events"]] == [3]
    assert second["data"]["cursor"] == {"after": 2, "next": 3, "more": False}


def test_runtime_events_do_not_advance_replication_cursor(tmp_path):
    store = EventStore(tmp_path)
    store.record_runtime("charger_connected", charger_id="charger-a")
    store.record_ocpp("charger-a", "BootNotification", {"charge_point_vendor": "Example"})
    store.record_runtime("charger_disconnected", charger_id="charger-a")

    payload = export_contract(tmp_path, after=0, limit=10)

    assert [event["id"] for event in payload["data"]["events"]] == [1]
    assert payload["data"]["cursor"]["next"] == 1


def test_export_normalizes_meter_values_and_includes_source_event_identity(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp(
        "charger-a",
        "MeterValues",
        {
            "connector_id": 1,
            "transaction_id": 7,
            "meter_value": [
                {
                    "timestamp": "2026-10-07T12:01:00Z",
                    "sampled_value": [
                        {"measurand": "Power.Active.Import", "unit": "W", "value": "7200"},
                        {"measurand": "Energy.Active.Import.Register", "unit": "Wh", "value": "1500"},
                    ],
                }
            ],
        },
        transaction_id=7,
    )

    payload = export_contract(tmp_path, after=0, limit=10)

    [sample] = payload["data"]["energy"]
    assert sample == {
        "source_event_id": 1,
        "sample_index": 0,
        "at": "2026-10-07T12:01:00Z",
        "charger_id": "charger-a",
        "connector_id": 1,
        "transaction_id": 7,
        "power_w": 7200.0,
        "energy_wh": 1500.0,
    }


def test_export_includes_current_snapshot_for_touched_transaction(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("charger-a", {"connector_id": 1, "id_tag": "CARD-A", "meter_start": 1000, "timestamp": "2026-10-07T12:00:00Z"})
    store.record_ocpp(
        "charger-a",
        "MeterValues",
        {"connector_id": 1, "transaction_id": transaction_id, "meter_value": []},
        transaction_id=transaction_id,
    )

    payload = export_contract(tmp_path, after=0, limit=10)

    [transaction] = payload["data"]["transactions"]
    assert transaction["id"] == transaction_id
    assert transaction["charger_id"] == "charger-a"
    assert transaction["connector_id"] == 1
    assert transaction["rfid"] == "CARD-A"
    assert transaction["active"] is True


def test_repeated_real_start_frames_have_distinct_export_event_ids(tmp_path):
    """Deduplicating transaction identity must not deduplicate received OCPP evidence."""
    store = EventStore(tmp_path)
    start = {
        "connector_id": 1,
        "id_tag": "CARD-A",
        "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    }
    store.record_ocpp("charger-a", "StartTransaction", start, transaction_id=7)
    store.record_ocpp("charger-a", "StartTransaction", start, transaction_id=7)

    exported = export_contract(tmp_path, after=0, limit=10)["data"]
    starts = [event for event in exported["events"] if event["action"] == "StartTransaction"]
    assert len(starts) == 2
    assert starts[0]["id"] != starts[1]["id"]
    assert exported["cursor"]["next"] == starts[-1]["id"]


def test_derived_transaction_boundary_does_not_create_ocpp_export_event(tmp_path):
    """The report may derive a boundary; the replication cursor tracks real OCPP only."""
    store = EventStore(tmp_path)
    start = {
        "connector_id": 1,
        "id_tag": "CARD-A",
        "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    }
    store.record_ocpp("charger-a", "StartTransaction", start, transaction_id=1)
    store.record_ocpp(
        "charger-a",
        "StartTransaction",
        {**start, "timestamp": "2026-10-03T10:05:00Z"},
        transaction_id=2,
    )
    first = export_contract(tmp_path, after=0, limit=10)["data"]
    replay = export_contract(tmp_path, after=0, limit=10)["data"]
    assert [event["action"] for event in first["events"]] == [
        "StartTransaction", "StartTransaction"
    ]
    assert first["cursor"]["next"] == 2
    assert replay["events"] == first["events"]
    assert replay["cursor"] == first["cursor"]


def test_later_distinct_start_preserves_open_state_and_missing_stop_in_export(tmp_path):
    store = EventStore(tmp_path)
    first = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "CARD-A", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    })
    second = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "CARD-B", "meter_start": 200,
        "timestamp": "2026-10-03T10:05:00Z",
    })

    data = export_contract(tmp_path, after=0, limit=20)["data"]
    transactions = {item["id"]: item for item in data["transactions"]}
    assert first != second
    assert transactions[first]["state"] == "open"
    assert transactions[first]["stopped_at"] is None
    assert transactions[first]["energy_wh"] is None
    assert transactions[first]["stop_received_at"] is None
    assert transactions[first]["quality"]["incomplete"] is True
    assert all(event["action"] != "StopTransaction" for event in data["events"])


def test_export_cursor_advances_for_late_real_stop_evidence(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "CARD-A", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    })
    first_page = export_contract(tmp_path, after=0, limit=20)["data"]
    first_cursor = first_page["cursor"]["next"]
    store.record_ocpp("charger-a", "StopTransaction", {
        "transaction_id": transaction_id, "meter_stop": 250,
        "timestamp": "2026-10-03T11:00:00Z",
    }, transaction_id=transaction_id)
    later_page = export_contract(tmp_path, after=first_cursor, limit=20)["data"]
    assert len(later_page["events"]) == 1
    assert later_page["events"][0]["action"] == "StopTransaction"
    assert later_page["cursor"]["next"] > first_cursor
    assert export_contract(tmp_path, after=first_cursor, limit=20)["data"]["events"] == later_page["events"]


def test_reporting_boundary_is_context_not_a_stop_and_other_connectors_are_independent(tmp_path):
    from ocpp_csms.transactions.query import TransactionQuery

    store = EventStore(tmp_path)
    first = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "A", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    })
    other_connector = store.commit_start("charger-a", {
        "connector_id": 2, "id_tag": "B", "meter_start": 200,
        "timestamp": "2026-10-03T10:01:00Z",
    })
    newer = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "C", "meter_start": 300,
        "timestamp": "2026-10-03T10:02:00Z",
    })

    query = TransactionQuery(tmp_path)
    old = query.get(first)
    separate = query.get(other_connector)
    assert old is not None and separate is not None
    assert old.status == "open"
    assert old.active is True
    assert old.record["stop_received_at"] is None
    assert old.record["stop"] is None
    assert old.record["energy_wh"] is None
    assert old.next_start_received_at == query.get(newer).record["start_received_at"]
    assert separate.next_start_received_at is None

    data = export_contract(tmp_path, after=0, limit=20)["data"]
    snapshot = next(item for item in data["transactions"] if item["id"] == first)
    assert snapshot["stopped_at"] is None
    assert snapshot["energy_wh"] is None
    assert snapshot["quality"]["incomplete"] is True


def test_reporting_does_not_depend_on_optional_json_archives(tmp_path):
    from ocpp_csms.transactions.query import TransactionQuery

    store = EventStore(tmp_path)
    transaction_id = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "A", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    })
    assert not list(tmp_path.rglob("*.json"))
    view = TransactionQuery(tmp_path).get(transaction_id)
    assert view is not None
    assert view.status == "open"
    snapshot = export_contract(tmp_path)["data"]["transactions"]
    assert len(snapshot) == 1
    assert snapshot[0]["id"] == transaction_id
