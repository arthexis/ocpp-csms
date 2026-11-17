from ocpp_csms.evidence.store import EventStore


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


