"""Red-first behavioral contract for issue #247.

These tests deliberately describe behavior not yet implemented on main.
Do not xfail/skip them: CI should stay red until the production refactor.
"""
import json
from datetime import timedelta

import pytest

from ocpp_csms.transactions.archive import TransactionArchive
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.transactions.query import TransactionQuery
from ocpp_csms.transactions.formatting import format_transaction, format_transactions, transaction_energy_wh

class SQLiteBackedArchive(TransactionArchive):
    """Test adapter: exercise SQLite start authority with optional JSON diagnostics."""

    async def start(self, charge_point_id, payload):
        transaction_id = EventStore(self.data_dir).commit_start(charge_point_id, payload)
        await self.diagnostic_start(charge_point_id, payload, transaction_id)
        return transaction_id




CP = "charger-a"


def start(*, timestamp="2026-10-03T10:00:00Z", connector=1, meter_start=100):
    return {
        "connector_id": connector,
        "id_tag": "card-a",
        "meter_start": meter_start,
        "timestamp": timestamp,
    }


def meter(transaction_id):
    return {
        "transaction_id": transaction_id,
        "connector_id": 1,
        "meter_value": [{
            "timestamp": "2026-10-03T10:02:00Z",
            "sampled_value": [{"value": "120"}],
        }],
    }


def stop(transaction_id):
    return {
        "transaction_id": transaction_id,
        "meter_stop": 150,
        "timestamp": "2026-10-03T10:10:00Z",
    }


def archives(root):
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (root / "transactions").glob("*/*.json")
    ]


def unresolved(root):
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (root / "transactions-unresolved").glob("*/*.json")
    ]


@pytest.mark.asyncio
async def test_distinct_starts_one_second_apart_are_both_retained(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    first = await archive.start(CP, start())
    second = await archive.start(CP, start(timestamp="2026-10-03T10:00:01Z"))
    assert second == first + 1
    records = {record["transaction_id"]: record for record in archives(tmp_path)}
    assert len(records) == 2
    assert records[first]["stop"] is None
    assert records[second]["stop"] is None


@pytest.mark.asyncio
async def test_exact_retry_beyond_sixty_seconds_uses_original_id(tmp_path, monkeypatch):
    import ocpp_csms.transactions.archive as archive_module

    original_datetime = archive_module.datetime
    class FutureDatetime(original_datetime):
        @classmethod
        def now(cls, tz=None):
            return original_datetime.now(tz) + timedelta(minutes=5)

    events = EventStore(tmp_path)
    archive = SQLiteBackedArchive(tmp_path)
    payload = start()
    first = events.commit_start(CP, payload)
    await archive.diagnostic_start(CP, payload, first)
    monkeypatch.setattr(archive_module, "datetime", FutureDatetime)
    assert events.commit_start(CP, payload) == first
    await archive.diagnostic_start(CP, payload, first)
    assert len(archives(tmp_path)) == 1


@pytest.mark.asyncio
async def test_exact_retry_after_restart_uses_original_id_regardless_of_age(tmp_path, monkeypatch):
    import ocpp_csms.transactions.archive as archive_module

    events = EventStore(tmp_path)
    archive = SQLiteBackedArchive(tmp_path)
    payload = start()
    first = events.commit_start(CP, payload)
    await archive.diagnostic_start(CP, payload, first)
    real_datetime = archive_module.datetime
    class FutureDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now(tz) + timedelta(days=1)

    monkeypatch.setattr(archive_module, "datetime", FutureDatetime)
    restarted = SQLiteBackedArchive(tmp_path)
    assert EventStore(tmp_path).commit_start(CP, payload) == first
    await restarted.diagnostic_start(CP, payload, first)
    assert len(archives(tmp_path)) == 1


@pytest.mark.asyncio
async def test_same_timestamp_with_different_evidence_gets_new_id(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    first = await archive.start(CP, start())
    second = await archive.start(CP, start(meter_start=101))
    assert second == first + 1
    assert len(archives(tmp_path)) == 2


@pytest.mark.asyncio
async def test_same_start_on_different_charge_point_is_not_deduplicated(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    first = await archive.start(CP, start())
    second = await archive.start("charger-b", start())
    assert second == first + 1


@pytest.mark.asyncio
async def test_unknown_meter_and_stop_are_orphan_evidence_not_transactions(tmp_path):
    store = EventStore(tmp_path)
    store.commit_transaction_evidence(CP, "MeterValues", meter(225))
    store.commit_transaction_evidence(CP, "StopTransaction", stop(225))
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
        assert db.execute("SELECT action FROM events ORDER BY id").fetchall() == [("MeterValues",), ("StopTransaction",)]


@pytest.mark.asyncio
async def test_new_start_never_persists_a_synthetic_stop_for_previous(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    first = await archive.start(CP, start())
    await archive.start(CP, start(timestamp="2026-10-03T10:05:00Z"))
    previous = next(row for row in archives(tmp_path) if row["transaction_id"] == first)
    assert previous["stop"] is None
    assert previous["status"] == "open"
    assert "recovery" not in previous


@pytest.mark.asyncio
async def test_query_derives_next_start_boundary_without_mutating_archive(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    first = await archive.start(CP, start())
    await archive.start(CP, start(timestamp="2026-10-03T10:05:00Z"))
    before = next(row for row in archives(tmp_path) if row["transaction_id"] == first)
    second = next(row for row in archives(tmp_path) if row["transaction_id"] != first)
    view = TransactionQuery(tmp_path).get(first)
    assert view is not None
    assert view.record["transaction_id"] == before["transaction_id"]
    assert view.record["start"] == before["start"]
    assert view.status == "open"
    assert before["stop"] is None
    assert before["status"] == "open"
    assert second["start_received_at"] is not None
    # Reporting derives the boundary without writing synthetic evidence.
    assert view.effective_stop_source == "next_start"
    assert view.effective_stop_at == second["start_received_at"]
    assert next(row for row in archives(tmp_path) if row["transaction_id"] == first) == before


@pytest.mark.asyncio
async def test_late_real_stop_takes_precedence_over_derived_boundary(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    first = await archive.start(CP, start())
    await archive.start(CP, start(timestamp="2026-10-03T10:05:00Z"))
    EventStore(tmp_path).commit_transaction_evidence(CP, "StopTransaction", stop(first))
    view = TransactionQuery(tmp_path).get(first)
    assert view.effective_stop_source == "observed"
    assert view.record["stop"]["transaction_id"] == first


@pytest.mark.asyncio
async def test_new_start_on_other_connector_does_not_infer_stop(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    first = await archive.start(CP, start(connector=1))
    await archive.start(CP, start(connector=2, timestamp="2026-10-03T10:05:00Z"))
    before = next(row for row in archives(tmp_path) if row["transaction_id"] == first)
    view = TransactionQuery(tmp_path).get(first)
    assert view is not None
    assert view.record["transaction_id"] == before["transaction_id"]
    assert view.record["start"] == before["start"]
    assert view.status == "open"
    assert before["stop"] is None
    assert view.effective_stop_source == "unresolved"
    assert view.effective_stop_at is None
    assert next(row for row in archives(tmp_path) if row["transaction_id"] == first) == before


@pytest.mark.asyncio
async def test_allocation_does_not_create_per_id_reservations(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    await archive.start(CP, start())
    await archive.start(CP, start(timestamp="2026-10-03T10:00:01Z"))
    assert not (tmp_path / "transaction-id-reservations").exists()


@pytest.mark.asyncio
async def test_monotonic_ids_survive_restart_with_incomplete_sessions(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    first = await archive.start(CP, start())
    second = await archive.start(CP, start(timestamp="2026-10-03T10:00:01Z"))
    restarted = SQLiteBackedArchive(tmp_path)
    third = await restarted.start(CP, start(timestamp="2026-10-03T10:00:02Z"))
    assert [first, second, third] == [1, 2, 3]
    assert all(record["stop"] is None for record in archives(tmp_path))


@pytest.mark.asyncio
async def test_unknown_evidence_does_not_advance_allocator_across_restart(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    await archive.meter_values(CP, meter(100000))
    await archive.stop(CP, stop(100001))
    restarted = SQLiteBackedArchive(tmp_path)
    assert await restarted.start(CP, start()) == 1


@pytest.mark.asyncio
async def test_distinct_start_on_other_charge_point_cannot_infer_boundary(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    first = await archive.start(CP, start())
    await archive.start("charger-b", start(timestamp="2026-10-03T10:05:00Z"))
    view = TransactionQuery(tmp_path).get(first)
    assert view.effective_stop_source == "unresolved"


@pytest.mark.asyncio
async def test_inferred_boundary_uses_receipt_time_not_charger_clock(tmp_path, monkeypatch):
    import ocpp_csms.transactions.archive as archive_module

    received = iter([
        "2026-10-03T10:01:00+00:00",
        "2026-10-03T10:07:00+00:00",
    ])
    monkeypatch.setattr(archive_module, "utc_now_iso", lambda: next(received))
    archive = SQLiteBackedArchive(tmp_path)
    first = await archive.start(CP, start(timestamp="2026-10-03T10:00:00Z"))
    await archive.start(CP, start(timestamp="2026-10-03T09:59:00Z"))
    records = {row["transaction_id"]: row for row in archives(tmp_path)}
    assert records[first]["start_received_at"] == "2026-10-03T10:01:00+00:00"
    successor = next(row for transaction_id, row in records.items() if transaction_id != first)
    assert successor["start_received_at"] == "2026-10-03T10:07:00+00:00"
    assert successor["start"]["timestamp"] == "2026-10-03T09:59:00Z"
    view = TransactionQuery(tmp_path).get(first)
    assert view is not None
    assert view.record["transaction_id"] == first
    assert view.record["start"] == records[first]["start"]
    assert view.effective_stop_source == "next_start"
    assert view.effective_stop_at == successor["start_received_at"]
    assert records[first]["stop"] is None
    assert next(row for row in archives(tmp_path) if row["transaction_id"] == first) == records[first]


@pytest.mark.asyncio
async def test_conflicting_late_stops_do_not_overwrite_first_stop(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start(CP, start())
    observed = stop(transaction_id)
    store.commit_transaction_evidence(CP, "StopTransaction", observed)
    store.commit_transaction_evidence(CP, "StopTransaction", {**observed, "meter_stop": 999})
    with store._connect() as db:
        assert db.execute("SELECT meter_stop FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone()[0] == 150
        payloads = [json.loads(row[0]) for row in db.execute("SELECT payload_json FROM events WHERE action = 'StopTransaction' ORDER BY id")]
    assert payloads == [observed, {**observed, "meter_stop": 999}]


@pytest.mark.asyncio
async def test_identical_stop_retry_does_not_change_observed_stop(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    transaction_id = await archive.start(CP, start())
    observed = stop(transaction_id)
    await archive.stop(CP, observed)
    await archive.stop(CP, observed)
    record = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    assert record["stop"] == observed
    assert unresolved(tmp_path) == []


@pytest.mark.asyncio
async def test_missing_meter_evidence_is_not_reported_as_zero_energy(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    first = await archive.start(CP, start())
    await archive.start(CP, start(timestamp="2026-10-03T10:05:00Z"))
    view = TransactionQuery(tmp_path).get(first)
    assert view.effective_stop_source == "next_start"
    assert view.record["stop"] is None
    assert view.record.get("meter_values", []) == []
    assert view.record.get("stop") is None
    # An inferred end is not a fabricated meterStop or a measured zero.
    assert transaction_energy_wh(view) is None
    table = format_transactions([view])
    header, row = table.splitlines()
    assert "ENERGY" in header
    assert row.split()[-2] == "-"
    detail = format_transaction(view)
    assert "Meter stop:   -" in detail
    assert "Energy:" not in detail
    assert "0 Wh" not in detail
    assert "0.000 kWh" not in detail


@pytest.mark.asyncio
async def test_exact_start_retry_does_not_change_archived_first_receipt(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    payload = start()
    transaction_id = await archive.start(CP, payload)
    before = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    assert await archive.start(CP, payload) == transaction_id
    after = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    assert after["start"] == before["start"]
    assert after["start_received_at"] == before["start_received_at"]
    assert len(archives(tmp_path)) == 1


@pytest.mark.asyncio
async def test_many_distinct_starts_do_not_collapse_or_reuse_ids(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    timestamps = [f"2026-10-03T10:00:{second:02d}Z" for second in range(20)]
    ids = [await archive.start(CP, start(timestamp=timestamp)) for timestamp in timestamps]
    assert len(set(ids)) == len(timestamps)
    assert ids == sorted(ids)
    assert len(archives(tmp_path)) == len(timestamps)


@pytest.mark.asyncio
async def test_unknown_stop_on_another_charge_point_never_changes_local_transaction(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    transaction_id = await archive.start(CP, start())
    before = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    EventStore(tmp_path).commit_transaction_evidence("charger-b", "StopTransaction", stop(transaction_id))
    after = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    assert after == before
    assert any(
        entry["charge_point_id"] == "charger-b"
        and entry["transaction_id"] == transaction_id
        for entry in unresolved(tmp_path)
    )


@pytest.mark.asyncio
async def test_completed_session_reports_measured_energy_consistently(tmp_path):
    """The actual stop reading, not a prior inferred boundary, determines energy."""
    archive = SQLiteBackedArchive(tmp_path)
    first = await archive.start(CP, start(meter_start=1000))
    EventStore(tmp_path).commit_transaction_evidence(CP, "StopTransaction", {
        "transaction_id": first, "meter_stop": 7420,
        "timestamp": "2026-10-03T10:10:30Z",
    })
    view = TransactionQuery(tmp_path).get(first)
    assert view is not None
    assert transaction_energy_wh(view) == 6420
    table = format_transactions([view])
    assert "6.420 kWh" in table
    detail = format_transaction(view)
    assert "Energy:       6.420 kWh" in detail
    assert "Meter start:  1000" in detail
    assert "Meter stop:   7420" in detail
    assert "Stopped:      2026-10-03T10:10:30Z" in detail
    assert "Duration:     10m 30s" in detail


@pytest.mark.asyncio
async def test_late_real_stop_reporting_uses_observed_time_and_energy(tmp_path):
    """A late stop is authoritative even when a later start suggests a boundary."""
    archive = SQLiteBackedArchive(tmp_path)
    first = await archive.start(CP, start(meter_start=1000))
    await archive.start(CP, start(
        timestamp="2026-10-03T10:05:00Z", meter_start=2000,
    ))
    EventStore(tmp_path).commit_transaction_evidence(CP, "StopTransaction", {
        "transaction_id": first, "meter_stop": 1600,
        "timestamp": "2026-10-03T10:10:30Z",
    })
    view = TransactionQuery(tmp_path).get(first)
    assert view is not None
    assert view.record["stop"]["meter_stop"] == 1600
    assert transaction_energy_wh(view) == 600
    assert "600 Wh" in format_transactions([view])
    detail = format_transaction(view)
    assert "Stopped:      2026-10-03T10:10:30Z" in detail
    assert "Duration:     10m 30s" in detail
    assert "Energy:       600 Wh" in detail
    assert view.effective_stop_source == "observed"
    assert view.record["stop"]["timestamp"] == "2026-10-03T10:10:30Z"


@pytest.mark.asyncio
async def test_retry_after_restart_preserves_first_receipt_and_archive(tmp_path, monkeypatch):
    store = EventStore(tmp_path)
    payload = start()
    original_id = store.commit_start(CP, payload)
    with store._connect() as db:
        original_receipt = db.execute("SELECT start_received_at FROM transactions WHERE transaction_id = ?", (original_id,)).fetchone()[0]
    restarted = EventStore(tmp_path)
    assert restarted.commit_start(CP, payload) == original_id
    with restarted._connect() as db:
        assert db.execute("SELECT start_received_at FROM transactions WHERE transaction_id = ?", (original_id,)).fetchone()[0] == original_receipt
        assert db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM events WHERE action = 'StartTransaction'").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_interleaved_charge_points_and_connectors_allocate_unique_ids(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    first = await archive.start(CP, start(connector=1))
    second = await archive.start("charger-b", start(connector=1))
    third = await archive.start(CP, start(connector=2))
    restarted = SQLiteBackedArchive(tmp_path)
    fourth = await restarted.start("charger-b", start(
        connector=2, timestamp="2026-10-03T10:01:00Z",
    ))
    assert [first, second, third, fourth] == sorted({first, second, third, fourth})
    records = {row["transaction_id"]: row for row in archives(tmp_path)}
    assert len(records) == 4
    assert [(records[transaction_id]["charge_point_id"],
             records[transaction_id]["start"]["connector_id"])
            for transaction_id in (first, second, third, fourth)] == [
        (CP, 1), ("charger-b", 1), (CP, 2), ("charger-b", 2),
    ]



def orphan_messages(root, message_type, transaction_id):
    """Inspect durable unresolved evidence without depending on file names."""
    return [
        row for row in unresolved(root)
        if row.get("message_type") == message_type
        and row.get("transaction_id") == transaction_id
    ]


@pytest.mark.asyncio
async def test_unknown_meter_is_preserved_with_payload_and_receipt_after_restart(tmp_path):
    store = EventStore(tmp_path)
    payload = meter(225)
    store.commit_transaction_evidence(CP, "MeterValues", payload)
    restarted = EventStore(tmp_path)
    with restarted._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
        row = db.execute("SELECT charger_id, received_at, payload_json FROM events WHERE action = 'MeterValues'").fetchone()
    assert row[0] == CP and row[1] and json.loads(row[2]) == payload


@pytest.mark.asyncio
async def test_unknown_stop_is_preserved_without_creating_transaction(tmp_path):
    store = EventStore(tmp_path)
    payload = stop(226)
    store.commit_transaction_evidence(CP, "StopTransaction", payload)
    with EventStore(tmp_path)._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
        row = db.execute("SELECT charger_id, received_at, payload_json FROM events WHERE action = 'StopTransaction'").fetchone()
    assert row[0] == CP and row[1] and json.loads(row[2]) == payload


@pytest.mark.asyncio
async def test_conflicting_stop_preserves_first_receipt_and_second_payload(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start(CP, start())
    first_stop = stop(transaction_id)
    store.commit_transaction_evidence(CP, "StopTransaction", first_stop)
    with store._connect() as db:
        original = db.execute("SELECT stop_received_at FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone()[0]
    conflict = {**first_stop, "meter_stop": 999}
    store.commit_transaction_evidence(CP, "StopTransaction", conflict)
    with store._connect() as db:
        assert db.execute("SELECT meter_stop, stop_received_at FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone() == (150, original)
        payloads = [json.loads(row[0]) for row in db.execute("SELECT payload_json FROM events WHERE action = 'StopTransaction' ORDER BY id")]
    assert payloads == [first_stop, conflict]


@pytest.mark.asyncio
async def test_identical_stop_retry_preserves_original_receipt(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start(CP, start())
    payload = stop(transaction_id)
    store.commit_transaction_evidence(CP, "StopTransaction", payload)
    with store._connect() as db:
        original = db.execute("SELECT stop_received_at FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone()[0]
    store.commit_transaction_evidence(CP, "StopTransaction", payload)
    with store._connect() as db:
        assert db.execute("SELECT stop_received_at FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone()[0] == original
        assert db.execute("SELECT COUNT(*) FROM events WHERE action = 'StopTransaction'").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_mismatched_connector_meter_does_not_change_local_transaction(tmp_path):
    archive = SQLiteBackedArchive(tmp_path)
    transaction_id = await archive.start(CP, start(connector=1))
    original = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    payload = {**meter(transaction_id), "connector_id": 2}
    EventStore(tmp_path).commit_transaction_evidence(CP, "MeterValues", payload)
    current = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    assert current == original
    assert any(row["payload"] == payload for row in orphan_messages(
        tmp_path, "MeterValues", transaction_id,
    ))


@pytest.mark.asyncio
async def test_orphan_external_id_not_adopted_after_restart(tmp_path):
    store = EventStore(tmp_path)
    store.commit_transaction_evidence("charger-b", "StopTransaction", stop(12345))
    local_id = EventStore(tmp_path).commit_start(CP, start())
    assert local_id == 1
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
        assert db.execute("SELECT charger_id, transaction_id FROM events WHERE action = 'StopTransaction'").fetchone() == ("charger-b", 12345)

