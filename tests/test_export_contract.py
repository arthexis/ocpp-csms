from __future__ import annotations

import json

from ocpp_csms.events import EventStore
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
    _transaction(tmp_path, 7)
    store.record_ocpp(
        "charger-a",
        "MeterValues",
        {"connector_id": 1, "transaction_id": 7, "meter_value": []},
        transaction_id=7,
    )

    payload = export_contract(tmp_path, after=0, limit=10)

    [transaction] = payload["data"]["transactions"]
    assert transaction["id"] == 7
    assert transaction["charger_id"] == "charger-a"
    assert transaction["connector_id"] == 1
    assert transaction["rfid"] == "CARD-A"
    assert transaction["active"] is True
