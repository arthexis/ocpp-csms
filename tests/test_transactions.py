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


@pytest.mark.asyncio
async def test_failed_write_after_skipping_recovered_id_does_not_consume_candidate(
    tmp_path, monkeypatch
):
    archive = TransactionArchive(tmp_path)
    assert await archive.start("charger-a", START) == 1
    await archive.stop(
        "charger-a",
        {
            "transaction_id": 2,
            "meter_stop": 150,
            "timestamp": "2026-10-02T12:10:00Z",
        },
    )

    next_start = {
        **START,
        "meter_start": 200,
        "timestamp": "2026-10-02T12:15:00Z",
    }
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
        {
            "transaction_id": transaction_id,
            "connector_id": 1,
            "meter_stop": 999,
            "timestamp": "2026-10-02T12:10:00Z",
        },
    )

    transaction_path = next((tmp_path / "transactions").glob("*/*.json"))
    local = json.loads(transaction_path.read_text(encoding="utf-8"))
    assert local["status"] == "open"
    assert local["stop"] is None

    unresolved_paths = list((tmp_path / "transactions-unresolved").glob("*/*.json"))
    assert len(unresolved_paths) == 1
    unresolved = json.loads(unresolved_paths[0].read_text(encoding="utf-8"))
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
        {
            "transaction_id": transaction_id,
            "connector_id": 2,
            "meter_value": [
                {
                    "timestamp": "2026-10-02T12:05:00Z",
                    "sampled_value": [{"value": "120"}],
                }
            ],
        },
    )

    transaction_path = next((tmp_path / "transactions").glob("*/*.json"))
    local = json.loads(transaction_path.read_text(encoding="utf-8"))
    assert local["meter_values"] == []

    unresolved_path = next((tmp_path / "transactions-unresolved").glob("*/*.json"))
    unresolved = json.loads(unresolved_path.read_text(encoding="utf-8"))
    assert unresolved["message_type"] == "MeterValues"
    assert unresolved["reason"] == "connector_mismatch"


@pytest.mark.asyncio
async def test_message_predating_local_start_is_preserved_as_unresolved(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start("charger-a", START)

    await archive.stop(
        "charger-a",
        {
            "transaction_id": transaction_id,
            "connector_id": 1,
            "meter_stop": 90,
            "timestamp": "2026-10-02T11:55:00Z",
        },
    )

    transaction_path = next((tmp_path / "transactions").glob("*/*.json"))
    local = json.loads(transaction_path.read_text(encoding="utf-8"))
    assert local["status"] == "open"
    assert local["stop"] is None

    unresolved_path = next((tmp_path / "transactions-unresolved").glob("*/*.json"))
    unresolved = json.loads(unresolved_path.read_text(encoding="utf-8"))
    assert unresolved["reason"] == "message_predates_local_start"


@pytest.mark.asyncio
async def test_matching_local_transaction_continues_normally(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start("charger-a", START)
    meter_values = {
        "transaction_id": transaction_id,
        "connector_id": 1,
        "meter_value": [
            {
                "timestamp": "2026-10-02T12:05:00Z",
                "sampled_value": [{"value": "120"}],
            }
        ],
    }
    stop = {
        "transaction_id": transaction_id,
        "connector_id": 1,
        "meter_stop": 150,
        "timestamp": "2026-10-02T12:10:00Z",
    }

    await archive.meter_values("charger-a", meter_values)
    await archive.stop("charger-a", stop)

    transaction_path = next((tmp_path / "transactions").glob("*/*.json"))
    local = json.loads(transaction_path.read_text(encoding="utf-8"))
    assert local["meter_values"] == [meter_values]
    assert local["stop"] == stop
    assert local["status"] == "stopped"
    assert not list((tmp_path / "transactions-unresolved").glob("*/*.json"))


@pytest.mark.asyncio
async def test_recovered_transaction_accepts_later_matching_evidence(tmp_path):
    archive = TransactionArchive(tmp_path)
    await archive.meter_values(
        "charger-a",
        {
            "transaction_id": 225,
            "connector_id": 1,
            "meter_value": [
                {
                    "timestamp": "2026-10-02T11:55:00Z",
                    "sampled_value": [{"value": "120"}],
                }
            ],
        },
    )
    await archive.stop(
        "charger-a",
        {
            "transaction_id": 225,
            "connector_id": 1,
            "meter_stop": 150,
            "timestamp": "2026-10-02T12:10:00Z",
        },
    )

    transaction_path = next((tmp_path / "transactions").glob("*/*.json"))
    recovered = json.loads(transaction_path.read_text(encoding="utf-8"))
    assert recovered["transaction_id"] == 225
    assert recovered["origin"] == "recovered"
    assert len(recovered["meter_values"]) == 1
    assert recovered["status"] == "stopped"
    assert not list((tmp_path / "transactions-unresolved").glob("*/*.json"))
