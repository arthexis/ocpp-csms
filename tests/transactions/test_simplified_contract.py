"""Red-first behavioral contract for issue #247.

These tests deliberately describe behavior not yet implemented on main.
Do not xfail/skip them: CI should stay red until the production refactor.
"""
import json
from datetime import timedelta

import pytest

from ocpp_csms.transactions.archive import TransactionArchive
from ocpp_csms.transactions.query import TransactionQuery
from ocpp_csms.transactions.formatting import format_transaction, format_transactions, transaction_energy_wh


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
    archive = TransactionArchive(tmp_path)
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

    archive = TransactionArchive(tmp_path)
    first = await archive.start(CP, start())
    monkeypatch.setattr(archive_module, "datetime", FutureDatetime)
    assert await archive.start(CP, start()) == first
    assert len(archives(tmp_path)) == 1


@pytest.mark.asyncio
async def test_exact_retry_after_restart_uses_original_id_regardless_of_age(tmp_path, monkeypatch):
    import ocpp_csms.transactions.archive as archive_module

    archive = TransactionArchive(tmp_path)
    first = await archive.start(CP, start())
    real_datetime = archive_module.datetime
    class FutureDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now(tz) + timedelta(days=1)

    monkeypatch.setattr(archive_module, "datetime", FutureDatetime)
    restarted = TransactionArchive(tmp_path)
    assert await restarted.start(CP, start()) == first
    assert len(archives(tmp_path)) == 1


@pytest.mark.asyncio
async def test_same_timestamp_with_different_evidence_gets_new_id(tmp_path):
    archive = TransactionArchive(tmp_path)
    first = await archive.start(CP, start())
    second = await archive.start(CP, start(meter_start=101))
    assert second == first + 1
    assert len(archives(tmp_path)) == 2


@pytest.mark.asyncio
async def test_same_start_on_different_charge_point_is_not_deduplicated(tmp_path):
    archive = TransactionArchive(tmp_path)
    first = await archive.start(CP, start())
    second = await archive.start("charger-b", start())
    assert second == first + 1


@pytest.mark.asyncio
async def test_unknown_meter_and_stop_are_orphan_evidence_not_transactions(tmp_path):
    archive = TransactionArchive(tmp_path)
    await archive.meter_values(CP, meter(225))
    await archive.stop(CP, stop(225))
    assert archives(tmp_path) == []
    messages = unresolved(tmp_path)
    assert {message["message_type"] for message in messages} == {
        "MeterValues", "StopTransaction"
    }
    assert all(message["transaction_id"] == 225 for message in messages)


@pytest.mark.asyncio
async def test_new_start_never_persists_a_synthetic_stop_for_previous(tmp_path):
    archive = TransactionArchive(tmp_path)
    first = await archive.start(CP, start())
    await archive.start(CP, start(timestamp="2026-10-03T10:05:00Z"))
    previous = next(row for row in archives(tmp_path) if row["transaction_id"] == first)
    assert previous["stop"] is None
    assert previous["status"] == "open"
    assert "recovery" not in previous


@pytest.mark.asyncio
async def test_query_derives_next_start_boundary_without_mutating_archive(tmp_path):
    archive = TransactionArchive(tmp_path)
    first = await archive.start(CP, start())
    await archive.start(CP, start(timestamp="2026-10-03T10:05:00Z"))
    before = next(row for row in archives(tmp_path) if row["transaction_id"] == first)
    second = next(row for row in archives(tmp_path) if row["transaction_id"] != first)
    view = TransactionQuery(tmp_path).get(first)
    assert view is not None
    assert view.record == before
    assert before["stop"] is None
    assert before["status"] == "open"
    assert second["start_received_at"] is not None
    # Reporting derives the boundary without writing synthetic evidence.
    assert view.effective_stop_source == "next_start"
    assert view.effective_stop_at == second["start_received_at"]
    assert next(row for row in archives(tmp_path) if row["transaction_id"] == first) == before


@pytest.mark.asyncio
async def test_late_real_stop_takes_precedence_over_derived_boundary(tmp_path):
    archive = TransactionArchive(tmp_path)
    first = await archive.start(CP, start())
    await archive.start(CP, start(timestamp="2026-10-03T10:05:00Z"))
    await archive.stop(CP, stop(first))
    view = TransactionQuery(tmp_path).get(first)
    assert view.effective_stop_source == "observed"
    assert view.record["stop"]["transaction_id"] == first


@pytest.mark.asyncio
async def test_new_start_on_other_connector_does_not_infer_stop(tmp_path):
    archive = TransactionArchive(tmp_path)
    first = await archive.start(CP, start(connector=1))
    await archive.start(CP, start(connector=2, timestamp="2026-10-03T10:05:00Z"))
    before = next(row for row in archives(tmp_path) if row["transaction_id"] == first)
    view = TransactionQuery(tmp_path).get(first)
    assert view is not None
    assert view.record == before
    assert before["stop"] is None
    assert view.effective_stop_source is None
    assert view.effective_stop_at is None
    assert next(row for row in archives(tmp_path) if row["transaction_id"] == first) == before


@pytest.mark.asyncio
async def test_allocation_does_not_create_per_id_reservations(tmp_path):
    archive = TransactionArchive(tmp_path)
    await archive.start(CP, start())
    await archive.start(CP, start(timestamp="2026-10-03T10:00:01Z"))
    assert not (tmp_path / "transaction-id-reservations").exists()


@pytest.mark.asyncio
async def test_monotonic_ids_survive_restart_with_incomplete_sessions(tmp_path):
    archive = TransactionArchive(tmp_path)
    first = await archive.start(CP, start())
    second = await archive.start(CP, start(timestamp="2026-10-03T10:00:01Z"))
    restarted = TransactionArchive(tmp_path)
    third = await restarted.start(CP, start(timestamp="2026-10-03T10:00:02Z"))
    assert [first, second, third] == [1, 2, 3]
    assert all(record["stop"] is None for record in archives(tmp_path))


@pytest.mark.asyncio
async def test_unknown_evidence_does_not_advance_allocator_across_restart(tmp_path):
    archive = TransactionArchive(tmp_path)
    await archive.meter_values(CP, meter(100000))
    await archive.stop(CP, stop(100001))
    restarted = TransactionArchive(tmp_path)
    assert await restarted.start(CP, start()) == 1


@pytest.mark.asyncio
async def test_distinct_start_on_other_charge_point_cannot_infer_boundary(tmp_path):
    archive = TransactionArchive(tmp_path)
    first = await archive.start(CP, start())
    await archive.start("charger-b", start(timestamp="2026-10-03T10:05:00Z"))
    view = TransactionQuery(tmp_path).get(first)
    assert view.effective_stop_source is None


@pytest.mark.asyncio
async def test_inferred_boundary_uses_receipt_time_not_charger_clock(tmp_path, monkeypatch):
    import ocpp_csms.transactions.archive as archive_module

    received = iter([
        "2026-10-03T10:01:00+00:00",
        "2026-10-03T10:07:00+00:00",
    ])
    monkeypatch.setattr(archive_module, "utc_now_iso", lambda: next(received))
    archive = TransactionArchive(tmp_path)
    first = await archive.start(CP, start(timestamp="2026-10-03T10:00:00Z"))
    await archive.start(CP, start(timestamp="2026-10-03T09:59:00Z"))
    records = {row["transaction_id"]: row for row in archives(tmp_path)}
    assert records[first]["start_received_at"] == "2026-10-03T10:01:00+00:00"
    successor = next(row for transaction_id, row in records.items() if transaction_id != first)
    assert successor["start_received_at"] == "2026-10-03T10:07:00+00:00"
    assert successor["start"]["timestamp"] == "2026-10-03T09:59:00Z"
    view = TransactionQuery(tmp_path).get(first)
    assert view is not None
    assert view.record == records[first]
    assert view.effective_stop_source == "next_start"
    assert view.effective_stop_at == successor["start_received_at"]
    assert records[first]["stop"] is None
    assert next(row for row in archives(tmp_path) if row["transaction_id"] == first) == records[first]


@pytest.mark.asyncio
async def test_conflicting_late_stops_do_not_overwrite_first_stop(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start(CP, start())
    observed = stop(transaction_id)
    await archive.stop(CP, observed)
    await archive.stop(CP, {**observed, "meter_stop": 999})
    record = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    assert record["stop"] == observed
    assert any(
        item["message_type"] == "StopTransaction"
        and item["transaction_id"] == transaction_id
        for item in unresolved(tmp_path)
    )


@pytest.mark.asyncio
async def test_identical_stop_retry_does_not_change_observed_stop(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start(CP, start())
    observed = stop(transaction_id)
    await archive.stop(CP, observed)
    await archive.stop(CP, observed)
    record = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    assert record["stop"] == observed
    assert unresolved(tmp_path) == []


@pytest.mark.asyncio
async def test_missing_meter_evidence_is_not_reported_as_zero_energy(tmp_path):
    archive = TransactionArchive(tmp_path)
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
    archive = TransactionArchive(tmp_path)
    payload = start()
    transaction_id = await archive.start(CP, payload)
    before = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    assert await archive.start(CP, payload) == transaction_id
    after = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    assert after["start"] == before["start"]
    assert after["start_received_at"] == before["start_received_at"]
    assert len(archives(tmp_path)) == 1


@pytest.mark.asyncio
async def test_json_diagnostic_failure_does_not_prevent_sqlite_authoritative_start(tmp_path, monkeypatch):
    """Optional JSON output cannot veto a committed authoritative start."""
    archive = TransactionArchive(tmp_path)

    def fail_json(*args, **kwargs):
        raise OSError("diagnostic JSON unavailable")

    monkeypatch.setattr(archive, "_write", fail_json)
    transaction_id = await archive.start(CP, start())
    assert transaction_id > 0
    # The committed SQLite transaction remains discoverable without JSON.
    from ocpp_csms.evidence.store import EventStore
    store = EventStore(tmp_path)
    assert store.find_recent_start(CP, start()) == transaction_id


@pytest.mark.asyncio
async def test_many_distinct_starts_do_not_collapse_or_reuse_ids(tmp_path):
    archive = TransactionArchive(tmp_path)
    timestamps = [f"2026-10-03T10:00:{second:02d}Z" for second in range(20)]
    ids = [await archive.start(CP, start(timestamp=timestamp)) for timestamp in timestamps]
    assert len(set(ids)) == len(timestamps)
    assert ids == sorted(ids)
    assert len(archives(tmp_path)) == len(timestamps)


@pytest.mark.asyncio
async def test_unknown_stop_on_another_charge_point_never_changes_local_transaction(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start(CP, start())
    before = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    await archive.stop("charger-b", stop(transaction_id))
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
    archive = TransactionArchive(tmp_path)
    first = await archive.start(CP, start(meter_start=1000))
    await archive.stop(CP, {
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
    archive = TransactionArchive(tmp_path)
    first = await archive.start(CP, start(meter_start=1000))
    await archive.start(CP, start(
        timestamp="2026-10-03T10:05:00Z", meter_start=2000,
    ))
    await archive.stop(CP, {
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
    assert view.effective_stop_at == "2026-10-03T10:10:30Z"


@pytest.mark.asyncio
async def test_retry_after_restart_preserves_first_receipt_and_archive(tmp_path, monkeypatch):
    import ocpp_csms.transactions.archive as archive_module

    times = iter(["2026-10-03T10:00:00+00:00", "2026-10-04T10:00:00+00:00"])
    monkeypatch.setattr(archive_module, "utc_now_iso", lambda: next(times))
    archive = TransactionArchive(tmp_path)
    payload = start()
    original_id = await archive.start(CP, payload)
    original = next(row for row in archives(tmp_path) if row["transaction_id"] == original_id)
    assert original["start_received_at"] == "2026-10-03T10:00:00+00:00"

    real_datetime = archive_module.datetime

    class NextDay(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now(tz) + timedelta(days=1)

    monkeypatch.setattr(archive_module, "datetime", NextDay)
    restarted = TransactionArchive(tmp_path)
    assert await restarted.start(CP, payload) == original_id
    assert archives(tmp_path) == [original]
    assert original["start"] == payload
    assert original["start_received_at"] == "2026-10-03T10:00:00+00:00"


@pytest.mark.asyncio
async def test_interleaved_charge_points_and_connectors_allocate_unique_ids(tmp_path):
    archive = TransactionArchive(tmp_path)
    first = await archive.start(CP, start(connector=1))
    second = await archive.start("charger-b", start(connector=1))
    third = await archive.start(CP, start(connector=2))
    restarted = TransactionArchive(tmp_path)
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


@pytest.mark.asyncio
async def test_sqlite_start_identity_survives_restart_without_json_archives(tmp_path):
    """JSON is diagnostic; restart deduplication must use SQLite alone."""
    archive = TransactionArchive(tmp_path)
    original_id = await archive.start(CP, start())
    assert original_id > 0
    for path in (tmp_path / "transactions").glob("*/*.json"):
        path.unlink()
    restarted = TransactionArchive(tmp_path)
    assert await restarted.start(CP, start()) == original_id



def orphan_messages(root, message_type, transaction_id):
    """Inspect durable unresolved evidence without depending on file names."""
    return [
        row for row in unresolved(root)
        if row.get("message_type") == message_type
        and row.get("transaction_id") == transaction_id
    ]


@pytest.mark.asyncio
async def test_unknown_meter_is_preserved_with_payload_and_receipt_after_restart(tmp_path):
    archive = TransactionArchive(tmp_path)
    payload = meter(225)
    await archive.meter_values(CP, payload)
    TransactionArchive(tmp_path)
    assert archives(tmp_path) == []
    evidence = orphan_messages(tmp_path, "MeterValues", 225)
    assert len(evidence) == 1
    assert evidence[0]["charge_point_id"] == CP
    assert evidence[0]["payload"] == payload
    assert isinstance(evidence[0]["received_at"], str)
    assert evidence[0]["received_at"]


@pytest.mark.asyncio
async def test_unknown_stop_is_preserved_without_creating_transaction(tmp_path):
    archive = TransactionArchive(tmp_path)
    payload = stop(226)
    await archive.stop(CP, payload)
    assert archives(tmp_path) == []
    evidence = orphan_messages(tmp_path, "StopTransaction", 226)
    assert len(evidence) == 1
    assert evidence[0]["charge_point_id"] == CP
    assert evidence[0]["payload"] == payload
    assert isinstance(evidence[0]["received_at"], str)
    assert evidence[0]["received_at"]


@pytest.mark.asyncio
async def test_conflicting_stop_preserves_first_receipt_and_second_payload(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start(CP, start())
    first_stop = stop(transaction_id)
    await archive.stop(CP, first_stop)
    original = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    conflict = {**first_stop, "meter_stop": 999}
    await archive.stop(CP, conflict)
    current = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    assert current["stop"] == first_stop
    assert current["stop_received_at"] == original["stop_received_at"]
    evidence = orphan_messages(tmp_path, "StopTransaction", transaction_id)
    assert any(row["payload"] == conflict for row in evidence)


@pytest.mark.asyncio
async def test_identical_stop_retry_preserves_original_receipt(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start(CP, start())
    payload = stop(transaction_id)
    await archive.stop(CP, payload)
    original = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    await archive.stop(CP, payload)
    current = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    assert current["stop"] == original["stop"]
    assert current["stop_received_at"] == original["stop_received_at"]
    assert orphan_messages(tmp_path, "StopTransaction", transaction_id) == []


@pytest.mark.asyncio
async def test_mismatched_connector_meter_does_not_change_local_transaction(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start(CP, start(connector=1))
    original = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    payload = {**meter(transaction_id), "connector_id": 2}
    await archive.meter_values(CP, payload)
    current = next(row for row in archives(tmp_path) if row["transaction_id"] == transaction_id)
    assert current == original
    assert any(row["payload"] == payload for row in orphan_messages(
        tmp_path, "MeterValues", transaction_id,
    ))


@pytest.mark.asyncio
async def test_orphan_external_id_not_adopted_after_restart(tmp_path):
    archive = TransactionArchive(tmp_path)
    await archive.stop("charger-b", stop(12345))
    restarted = TransactionArchive(tmp_path)
    local_id = await restarted.start(CP, start())
    assert local_id == 1
    records = archives(tmp_path)
    assert len(records) == 1
    assert records[0]["transaction_id"] == local_id
    assert records[0]["start"] == start()
    assert orphan_messages(tmp_path, "StopTransaction", 12345)
