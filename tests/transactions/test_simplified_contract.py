"""Red-first behavioral contract for issue #247.

These tests deliberately describe behavior not yet implemented on main.
Do not xfail/skip them: CI should stay red until the production refactor.
"""
import json
from datetime import timedelta

import pytest

from ocpp_csms.transactions.archive import TransactionArchive
from ocpp_csms.transactions.query import TransactionQuery


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
    # meter_start alone cannot establish delivered energy without an end reading.
    assert getattr(view, "energy_wh", None) is None


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
async def test_failed_start_write_does_not_publish_id_or_partial_transaction(tmp_path, monkeypatch):
    archive = TransactionArchive(tmp_path)
    original_write = archive._write

    def fail_write(path, record):
        raise OSError("simulated interrupted archive write")

    monkeypatch.setattr(archive, "_write", fail_write)
    with pytest.raises(OSError, match="interrupted archive write"):
        await archive.start(CP, start())
    assert archives(tmp_path) == []

    monkeypatch.setattr(archive, "_write", original_write)
    first = await archive.start(CP, start(timestamp="2026-10-03T10:00:01Z"))
    assert first > 0
    assert len(archives(tmp_path)) == 1


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
