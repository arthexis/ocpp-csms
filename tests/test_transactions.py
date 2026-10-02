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
