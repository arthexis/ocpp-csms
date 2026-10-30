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


def test_cursor_pages_replay_retries_and_orphan_evidence_without_gaps(tmp_path):
    store = EventStore(tmp_path)
    start = {
        "connector_id": 1, "id_tag": "CARD-A", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    }
    transaction_id = store.commit_start("charger-a", start)
    assert store.commit_start("charger-a", start) == transaction_id
    store.commit_transaction_evidence("charger-a", "MeterValues", {
        "connector_id": 1, "transaction_id": 99999, "meter_value": [],
    })
    store.commit_transaction_evidence("charger-a", "StopTransaction", {
        "transaction_id": 99999, "meter_stop": 150,
        "timestamp": "2026-10-03T10:10:00Z",
    })
    store.commit_transaction_evidence("charger-a", "StopTransaction", {
        "transaction_id": transaction_id, "meter_stop": 200,
        "timestamp": "2026-10-03T10:15:00Z",
    })

    cursor = 0
    collected = []
    while True:
        page = export_contract(tmp_path, after=cursor, limit=2)["data"]
        replay = export_contract(tmp_path, after=cursor, limit=2)["data"]
        assert replay["events"] == page["events"]
        assert replay["cursor"] == page["cursor"]
        ids = [event["id"] for event in page["events"]]
        assert ids == sorted(ids)
        assert all(event_id > cursor for event_id in ids)
        collected.extend(page["events"])
        assert page["cursor"]["next"] == (ids[-1] if ids else cursor)
        cursor = page["cursor"]["next"]
        if not page["cursor"]["more"]:
            break

    assert len(collected) == 5
    assert len({event["id"] for event in collected}) == 5
    assert [event["action"] for event in collected] == [
        "StartTransaction", "StartTransaction", "MeterValues",
        "StopTransaction", "StopTransaction",
    ]
    assert [event["transaction_id"] for event in collected] == [
        transaction_id, transaction_id, 99999, 99999, transaction_id,
    ]
    assert export_contract(tmp_path, after=cursor, limit=2)["data"]["events"] == []


def test_late_stop_after_previous_cursor_is_exported_as_new_evidence(tmp_path):
    store = EventStore(tmp_path)
    first = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "CARD-A", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    })
    store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "CARD-B", "meter_start": 200,
        "timestamp": "2026-10-03T10:05:00Z",
    })
    prior = export_contract(tmp_path, after=0, limit=10)["data"]
    cursor = prior["cursor"]["next"]
    store.commit_transaction_evidence("charger-a", "StopTransaction", {
        "transaction_id": first, "meter_stop": 180,
        "timestamp": "2026-10-03T10:04:00Z",
    })
    new = export_contract(tmp_path, after=cursor, limit=10)["data"]
    assert len(new["events"]) == 1
    assert new["events"][0]["action"] == "StopTransaction"
    assert new["events"][0]["transaction_id"] == first
    assert new["cursor"]["next"] > cursor
    assert export_contract(tmp_path, after=cursor, limit=10)["data"]["events"] == new["events"]


def test_export_uses_one_sqlite_snapshot_for_events_and_transactions(tmp_path, monkeypatch):
    """A concurrent stop committed after the page read must wait for the next export."""
    import ocpp_csms.export_contract as exporter

    store = EventStore(tmp_path)
    transaction_id = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "CARD-A", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    })
    # Production initialization enables WAL, so a writer can commit during export.
    original = exporter._event_rows

    def read_then_commit(connection, *, after, limit):
        rows, more = original(connection, after=after, limit=limit)
        store.commit_transaction_evidence("charger-a", "StopTransaction", {
            "transaction_id": transaction_id, "meter_stop": 250,
            "timestamp": "2026-10-03T11:00:00Z",
        })
        return rows, more

    monkeypatch.setattr(exporter, "_event_rows", read_then_commit)
    first = exporter.export_contract(tmp_path)["data"]
    assert [event["action"] for event in first["events"]] == ["StartTransaction"]
    assert first["transactions"][0]["state"] == "open"
    assert first["transactions"][0]["stopped_at"] is None

    monkeypatch.setattr(exporter, "_event_rows", original)
    later = exporter.export_contract(tmp_path, after=first["cursor"]["next"])["data"]
    assert [event["action"] for event in later["events"]] == ["StopTransaction"]
    assert later["transactions"][0]["state"] == "stopped"


def test_export_reader_does_not_block_concurrent_charger_commit(tmp_path, monkeypatch):
    """A held WAL snapshot must not delay a separate charger write."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import ocpp_csms.export_contract as exporter

    store = EventStore(tmp_path)
    transaction_id = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "CARD-A", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    })
    snapshot_ready = Event()
    release_snapshot = Event()
    original = exporter._event_rows

    def held_snapshot(connection, *, after, limit):
        result = original(connection, after=after, limit=limit)
        snapshot_ready.set()
        if not release_snapshot.wait(timeout=10):
            raise AssertionError("timed out waiting to release export snapshot")
        return result

    monkeypatch.setattr(exporter, "_event_rows", held_snapshot)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            export_future = executor.submit(exporter.export_contract, tmp_path)
            assert snapshot_ready.wait(timeout=10)
            writer_future = executor.submit(
                store.commit_transaction_evidence, "charger-a", "StopTransaction",
                {"transaction_id": transaction_id, "meter_stop": 200,
                 "timestamp": "2026-10-03T10:10:00Z"},
            )
            # The writer must finish while the read transaction is still open.
            writer_future.result(timeout=5)
            release_snapshot.set()
            first = export_future.result(timeout=10)["data"]
    finally:
        release_snapshot.set()
        monkeypatch.setattr(exporter, "_event_rows", original)

    assert [event["action"] for event in first["events"]] == ["StartTransaction"]
    assert first["transactions"][0]["state"] == "open"
    second = exporter.export_contract(tmp_path, after=first["cursor"]["next"])["data"]
    assert [event["action"] for event in second["events"]] == ["StopTransaction"]
    assert second["transactions"][0]["state"] == "stopped"

def test_export_status_uses_same_sqlite_snapshot_as_events(tmp_path, monkeypatch):
    """Status must not see evidence committed after the event page snapshot."""
    import ocpp_csms.export_contract as exporter

    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "BootNotification", {"charge_point_vendor": "Example"})
    original = exporter._event_rows

    def read_then_connect(connection, *, after, limit):
        result = original(connection, after=after, limit=limit)
        store.record_runtime("charger_connected", charger_id="charger-b")
        return result

    monkeypatch.setattr(exporter, "_event_rows", read_then_connect)
    first = exporter.export_contract(tmp_path)["data"]
    assert "charger-b" not in {charger["id"] for charger in first["status"]["chargers"]}

    monkeypatch.setattr(exporter, "_event_rows", original)
    second = exporter.export_contract(tmp_path)["data"]
    assert "charger-b" in {charger["id"] for charger in second["status"]["chargers"]}


def test_failed_export_releases_snapshot_and_preserves_following_evidence(tmp_path, monkeypatch):
    """A reader failure must not retain its snapshot or prevent subsequent writes."""
    import sqlite3
    import pytest
    import ocpp_csms.export_contract as exporter
    from ocpp_csms.schema import database_path

    store = EventStore(tmp_path)
    transaction_id = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "CARD-A", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    })
    initial = exporter.export_contract(tmp_path)["data"]
    cursor = initial["cursor"]["next"]
    original = exporter._transaction_snapshots

    def fail_after_read(data_dir, rows, connection):
        raise RuntimeError("interrupted export")

    monkeypatch.setattr(exporter, "_transaction_snapshots", fail_after_read)
    with pytest.raises(RuntimeError, match="interrupted export"):
        exporter.export_contract(tmp_path, after=0)
    monkeypatch.setattr(exporter, "_transaction_snapshots", original)

    store.commit_transaction_evidence("charger-a", "MeterValues", {
        "connector_id": 1, "transaction_id": transaction_id, "meter_value": [],
    })
    store.commit_transaction_evidence("charger-a", "StopTransaction", {
        "transaction_id": transaction_id, "meter_stop": 150,
        "timestamp": "2026-10-03T10:10:00Z",
    })
    next_page = exporter.export_contract(tmp_path, after=cursor)["data"]
    assert [event["action"] for event in next_page["events"]] == ["MeterValues", "StopTransaction"]
    assert next_page["transactions"][0]["state"] == "stopped"
    assert len({event["id"] for event in next_page["events"]}) == 2
    with sqlite3.connect(database_path(tmp_path)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_export_transaction_timestamps_are_utc_or_null(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "TEST", "meter_start": 100,
        "timestamp": "2026-01-01T00:00:00Z",
    })
    store.commit_transaction_evidence("charger-a", "StopTransaction", {
        "transaction_id": transaction_id, "meter_stop": 200,
        "timestamp": "2026-01-01T00:10:00Z",
    })
    from datetime import datetime, timezone
    transaction = export_contract(tmp_path)["data"]["transactions"][0]
    for field in ("started_at", "stopped_at", "last_activity_at"):
        value = transaction[field]
        assert value is None or datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
        if value is not None:
            assert datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() == timezone.utc.utcoffset(None)


def test_soc_export_metadata_uses_only_measured_valid_samples():
    from ocpp_csms.transactions.export_metadata import transaction_export_metadata

    def meter(*values):
        return {"meter_value": [{"sampled_value": list(values)}]}

    sample = lambda value, unit="Percent": {"measurand": "SoC", "unit": unit, "value": value}
    record = {"status": "open", "start": None, "stop": None,
              "meter_values": [meter(sample("23"), sample("-10"), sample("100", "kWh")),
                               meter(sample("81.6"))]}
    metadata = transaction_export_metadata(record)
    assert metadata["soc_start"] == 23
    assert metadata["soc_end"] == 82
    assert metadata["quality"]["incomplete"] is True
    assert transaction_export_metadata({"meter_values": [meter(sample("41"))]})["soc_start"] is None
    assert transaction_export_metadata({"meter_values": [meter(sample("41"))]})["soc_end"] == 41
    assert transaction_export_metadata({"meter_values": [meter(sample("NaN"), sample("inf"))]})["soc_end"] is None
    assert transaction_export_metadata({"meter_values": []})["soc_start"] is None
    assert transaction_export_metadata({"meter_values": []})["soc_end"] is None
