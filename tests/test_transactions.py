import json

import pytest

from ocpp_csms.transactions import TransactionArchive


START = {
    "connector_id": 1,
    "id_tag": "card-a",
    "meter_start": 100,
    "timestamp": "2026-10-02T12:00:00Z",
}


@pytest.mark.asyncio
async def test_exact_start_retry_survives_archive_restart(tmp_path):
    archive = TransactionArchive(tmp_path)

    first = await archive.start("charger-a", START)
    restarted = TransactionArchive(tmp_path)
    retry = await restarted.start("charger-a", START)

    assert first == 1
    assert retry == first
    assert len(list((tmp_path / "transactions").glob("*/*.json"))) == 1


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
    next_start = {
        **START,
        "meter_start": 200,
        "timestamp": "2026-10-02T12:05:00Z",
    }
    assert await restarted.start("charger-a", next_start) == 2


@pytest.mark.asyncio
async def test_transaction_records_persist_local_and_recovered_origin(tmp_path):
    archive = TransactionArchive(tmp_path)

    local_id = await archive.start("charger-a", START)
    await archive.stop(
        "charger-a",
        {
            "transaction_id": 225,
            "meter_stop": 150,
            "timestamp": "2026-10-02T12:10:00Z",
        },
    )

    records = {
        record["transaction_id"]: record
        for path in (tmp_path / "transactions").glob("*/*.json")
        for record in [json.loads(path.read_text(encoding="utf-8"))]
    }

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
    await archive.stop(
        "charger-a",
        {
            "transaction_id": 225,
            "meter_stop": 150,
            "timestamp": "2026-10-02T12:10:00Z",
        },
    )

    updated = json.loads(path.read_text(encoding="utf-8"))
    assert updated["origin"] == "recovered"


@pytest.mark.asyncio
async def test_recovered_id_does_not_advance_local_allocator(tmp_path):
    archive = TransactionArchive(tmp_path)
    await archive.stop(
        "charger-a",
        {
            "transaction_id": 225,
            "meter_stop": 150,
            "timestamp": "2026-10-02T12:10:00Z",
        },
    )

    assert await archive.start("charger-a", START) == 1


@pytest.mark.asyncio
async def test_recovered_ids_do_not_advance_allocator_after_restart(tmp_path):
    archive = TransactionArchive(tmp_path)
    for transaction_id in (225, 221, 218):
        await archive.stop(
            "charger-a",
            {
                "transaction_id": transaction_id,
                "meter_stop": transaction_id,
                "timestamp": "2026-10-02T12:10:00Z",
            },
        )

    restarted = TransactionArchive(tmp_path)
    assert await restarted.start("charger-a", START) == 1


@pytest.mark.asyncio
async def test_restart_advances_from_highest_local_id_only(tmp_path):
    archive = TransactionArchive(tmp_path)
    assert await archive.start("charger-a", START) == 1
    await archive.stop(
        "charger-a",
        {
            "transaction_id": 225,
            "meter_stop": 150,
            "timestamp": "2026-10-02T12:10:00Z",
        },
    )

    restarted = TransactionArchive(tmp_path)
    next_start = {
        **START,
        "meter_start": 200,
        "timestamp": "2026-10-02T12:05:00Z",
    }
    assert await restarted.start("charger-a", next_start) == 2


@pytest.mark.asyncio
async def test_local_allocator_skips_recovered_id_when_sequence_reaches_it(tmp_path):
    archive = TransactionArchive(tmp_path)
    await archive.stop(
        "charger-a",
        {
            "transaction_id": 3,
            "meter_stop": 150,
            "timestamp": "2026-10-02T12:10:00Z",
        },
    )

    assert await archive.start("charger-a", START) == 1
    assert await archive.start(
        "charger-a",
        {**START, "meter_start": 200, "timestamp": "2026-10-02T12:05:00Z"},
    ) == 2
    assert await archive.start(
        "charger-a",
        {**START, "meter_start": 300, "timestamp": "2026-10-02T12:10:00Z"},
    ) == 4


@pytest.mark.asyncio
async def test_local_allocator_skips_recovered_id_after_restart(tmp_path):
    archive = TransactionArchive(tmp_path)
    assert await archive.start("charger-a", START) == 1
    assert await archive.start(
        "charger-a",
        {**START, "meter_start": 200, "timestamp": "2026-10-02T12:05:00Z"},
    ) == 2
    await archive.stop(
        "charger-a",
        {
            "transaction_id": 3,
            "meter_stop": 150,
            "timestamp": "2026-10-02T12:10:00Z",
        },
    )

    restarted = TransactionArchive(tmp_path)
    assert await restarted.start(
        "charger-a",
        {**START, "meter_start": 300, "timestamp": "2026-10-02T12:15:00Z"},
    ) == 4
