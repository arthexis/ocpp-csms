import json

import pytest

from ocpp_csms.transactions.archive import TransactionArchive


START = {
    "connector_id": 1,
    "id_tag": "card-a",
    "meter_start": 100,
    "timestamp": "2026-10-02T12:00:00Z",
}


def start_payload(*, meter_start=100, timestamp="2026-10-02T12:00:00Z"):
    return {**START, "meter_start": meter_start, "timestamp": timestamp}


def stop_payload(
    transaction_id,
    *,
    meter_stop=150,
    timestamp="2026-10-02T12:10:00Z",
    connector_id=None,
):
    payload = {
        "transaction_id": transaction_id,
        "meter_stop": meter_stop,
        "timestamp": timestamp,
    }
    if connector_id is not None:
        payload["connector_id"] = connector_id
    return payload


def meter_payload(
    transaction_id,
    *,
    connector_id=1,
    timestamp="2026-10-02T12:05:00Z",
    value="120",
):
    return {
        "transaction_id": transaction_id,
        "connector_id": connector_id,
        "meter_value": [
            {
                "timestamp": timestamp,
                "sampled_value": [{"value": value}],
            }
        ],
    }


def json_records(root):
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in root.glob("*/*.json")
    ]


def transaction_records(tmp_path):
    return json_records(tmp_path / "transactions")


def unresolved_records(tmp_path):
    return json_records(tmp_path / "transactions-unresolved")


def only_transaction(tmp_path):
    [record] = transaction_records(tmp_path)
    return record


def only_unresolved(tmp_path):
    [record] = unresolved_records(tmp_path)
    return record


@pytest.mark.asyncio
async def test_exact_start_retry_survives_archive_restart(tmp_path):
    archive = TransactionArchive(tmp_path)

    first = await archive.start("charger-a", START)
    restarted = TransactionArchive(tmp_path)
    retry = await restarted.start("charger-a", START)

    assert first == 1
    assert retry == first
    assert len(transaction_records(tmp_path)) == 1


@pytest.mark.asyncio
async def test_failed_start_write_does_not_consume_transaction_id(tmp_path, monkeypatch):
    archive = TransactionArchive(tmp_path)
    original_write = archive._write

    def fail_write(path, record):
        raise OSError("disk unavailable")

    monkeypatch.setattr(archive, "_write", fail_write)
    with pytest.raises(OSError, match="disk unavailable"):
        await archive.start("charger-a", START)

    monkeypatch.setattr(archive, "_write", original_write)
    assert await archive.start("charger-a", START) == 1

    restarted = TransactionArchive(tmp_path)
    assert await restarted.start(
        "charger-a",
        start_payload(meter_start=200, timestamp="2026-10-02T12:05:00Z"),
    ) == 2


@pytest.mark.asyncio
async def test_transaction_records_persist_local_and_recovered_origin(tmp_path):
    archive = TransactionArchive(tmp_path)

    local_id = await archive.start("charger-a", START)
    await archive.stop("charger-a", stop_payload(225))

    records = {record["transaction_id"]: record for record in transaction_records(tmp_path)}

    assert records[local_id]["origin"] == "local"
    assert records[225]["origin"] == "recovered"


@pytest.mark.asyncio
async def test_legacy_recovered_record_gets_inferred_origin_when_updated(tmp_path):
    transactions_dir = tmp_path / "transactions" / "2026-10-02"
    transactions_dir.mkdir(parents=True)
    path = transactions_dir / "charger-a-225.json"
    path.write_text(
        json.dumps(
            {
                "transaction_id": 225,
                "charge_point_id": "charger-a",
                "status": "recovered",
                "created_at": "2026-10-02T12:00:00Z",
                "updated_at": "2026-10-02T12:00:00Z",
                "id_tag": None,
                "start": None,
                "meter_values": [],
                "stop": None,
            }
        ),
        encoding="utf-8",
    )

    archive = TransactionArchive(tmp_path)
    await archive.stop("charger-a", stop_payload(225))

    updated = json.loads(path.read_text(encoding="utf-8"))
    assert updated["origin"] == "recovered"


@pytest.mark.asyncio
async def test_recovered_id_does_not_advance_local_allocator(tmp_path):
    archive = TransactionArchive(tmp_path)
    await archive.stop("charger-a", stop_payload(225))

    assert await archive.start("charger-a", START) == 1


@pytest.mark.asyncio
async def test_recovered_ids_do_not_advance_allocator_after_restart(tmp_path):
    archive = TransactionArchive(tmp_path)
    for transaction_id in (225, 221, 218):
        await archive.stop("charger-a", stop_payload(transaction_id, meter_stop=transaction_id))

    restarted = TransactionArchive(tmp_path)
    assert await restarted.start("charger-a", START) == 1


@pytest.mark.asyncio
async def test_restart_advances_from_highest_local_id_only(tmp_path):
    archive = TransactionArchive(tmp_path)
    assert await archive.start("charger-a", START) == 1
    await archive.stop("charger-a", stop_payload(225))

    restarted = TransactionArchive(tmp_path)
    assert await restarted.start(
        "charger-a",
        start_payload(meter_start=200, timestamp="2026-10-02T12:05:00Z"),
    ) == 2


@pytest.mark.asyncio
async def test_local_allocator_skips_recovered_id_when_sequence_reaches_it(tmp_path):
    archive = TransactionArchive(tmp_path)
    await archive.stop("charger-a", stop_payload(3))

    assert await archive.start("charger-a", START) == 1
    assert await archive.start(
        "charger-a",
        start_payload(meter_start=200, timestamp="2026-10-02T12:05:00Z"),
    ) == 2
    assert await archive.start(
        "charger-a",
        start_payload(meter_start=300, timestamp="2026-10-02T12:10:00Z"),
    ) == 4


@pytest.mark.asyncio
async def test_local_allocator_skips_recovered_id_after_restart(tmp_path):
    archive = TransactionArchive(tmp_path)
    assert await archive.start("charger-a", START) == 1
    assert await archive.start(
        "charger-a",
        start_payload(meter_start=200, timestamp="2026-10-02T12:05:00Z"),
    ) == 2
    await archive.stop("charger-a", stop_payload(3))

    restarted = TransactionArchive(tmp_path)
    assert await restarted.start(
        "charger-a",
        start_payload(meter_start=300, timestamp="2026-10-02T12:15:00Z"),
    ) == 4


@pytest.mark.asyncio
async def test_failed_write_after_skipping_recovered_id_does_not_consume_candidate(
    tmp_path, monkeypatch
):
    archive = TransactionArchive(tmp_path)
    assert await archive.start("charger-a", START) == 1
    await archive.stop("charger-a", stop_payload(2))

    next_start = start_payload(meter_start=200, timestamp="2026-10-02T12:15:00Z")
    original_write = archive._write

    def fail_write(path, record):
        if record.get("origin") == "local" and record.get("transaction_id") == 3:
            raise OSError("disk unavailable")
        original_write(path, record)

    monkeypatch.setattr(archive, "_write", fail_write)
    with pytest.raises(OSError, match="disk unavailable"):
        await archive.start("charger-a", next_start)

    monkeypatch.setattr(archive, "_write", original_write)
    assert await archive.start("charger-a", next_start) == 3


@pytest.mark.asyncio
async def test_conflicting_charge_point_does_not_mutate_local_transaction(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start("charger-a", START)

    await archive.stop(
        "charger-b",
        stop_payload(transaction_id, meter_stop=999, connector_id=1),
    )

    local = only_transaction(tmp_path)
    assert local["status"] == "open"
    assert local["stop"] is None

    unresolved = only_unresolved(tmp_path)
    assert unresolved["transaction_id"] == transaction_id
    assert unresolved["charge_point_id"] == "charger-b"
    assert unresolved["message_type"] == "StopTransaction"
    assert unresolved["reason"] == "charge_point_mismatch"


@pytest.mark.asyncio
async def test_conflicting_connector_does_not_mutate_local_transaction(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start("charger-a", START)

    await archive.meter_values(
        "charger-a",
        meter_payload(transaction_id, connector_id=2),
    )

    assert only_transaction(tmp_path)["meter_values"] == []
    unresolved = only_unresolved(tmp_path)
    assert unresolved["message_type"] == "MeterValues"
    assert unresolved["reason"] == "connector_mismatch"


@pytest.mark.asyncio
async def test_message_predating_local_start_is_preserved_as_unresolved(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start("charger-a", START)

    await archive.stop(
        "charger-a",
        stop_payload(
            transaction_id,
            meter_stop=90,
            timestamp="2026-10-02T11:55:00Z",
            connector_id=1,
        ),
    )

    local = only_transaction(tmp_path)
    assert local["status"] == "open"
    assert local["stop"] is None
    assert only_unresolved(tmp_path)["reason"] == "message_predates_local_start"


@pytest.mark.asyncio
async def test_matching_local_transaction_continues_normally(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start("charger-a", START)
    meter_values = meter_payload(transaction_id)
    stop = stop_payload(transaction_id, connector_id=1)

    await archive.meter_values("charger-a", meter_values)
    await archive.stop("charger-a", stop)

    local = only_transaction(tmp_path)
    assert local["meter_values"] == [meter_values]
    assert local["stop"] == stop
    assert local["status"] == "stopped"
    assert unresolved_records(tmp_path) == []


@pytest.mark.asyncio
async def test_recovered_transaction_accepts_later_matching_evidence(tmp_path):
    archive = TransactionArchive(tmp_path)
    await archive.meter_values(
        "charger-a",
        meter_payload(225, timestamp="2026-10-02T11:55:00Z"),
    )
    await archive.stop("charger-a", stop_payload(225, connector_id=1))

    recovered = only_transaction(tmp_path)
    assert recovered["transaction_id"] == 225
    assert recovered["origin"] == "recovered"
    assert len(recovered["meter_values"]) == 1
    assert recovered["status"] == "stopped"
    assert unresolved_records(tmp_path) == []
