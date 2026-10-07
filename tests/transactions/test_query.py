import json
from datetime import datetime, timezone

import pytest

from ocpp_csms.transaction_query import TransactionQuery
from ocpp_csms.transactions import TransactionArchive


CHARGER_A = "charger-a"
CHARGER_B = "charger-b"


def start_payload(*, connector=1, id_tag="card-a", timestamp="2026-10-03T10:00:00Z"):
    return {
        "connector_id": connector,
        "id_tag": id_tag,
        "meter_start": 100,
        "timestamp": timestamp,
    }


def stop_payload(transaction_id, *, timestamp="2026-10-03T10:10:00Z", **extra):
    return {
        "transaction_id": transaction_id,
        "meter_stop": 200,
        "timestamp": timestamp,
        **extra,
    }


@pytest.mark.asyncio
async def test_lists_newest_first_and_gets_one_transaction(tmp_path):
    archive = TransactionArchive(tmp_path)
    first = await archive.start(CHARGER_A, start_payload(timestamp="2026-10-03T10:00:00Z"))
    await archive.stop(CHARGER_A, stop_payload(first, timestamp="2026-10-03T10:10:00Z"))
    second = await archive.start(CHARGER_A, start_payload(id_tag="card-b", timestamp="2026-10-03T11:00:00Z"))

    query = TransactionQuery(tmp_path)

    assert [view.transaction_id for view in query.list()] == [second, first]
    assert query.get(first).status == "stopped"
    assert query.get(9999) is None


@pytest.mark.asyncio
async def test_active_and_last_never_return_same_transaction(tmp_path):
    archive = TransactionArchive(tmp_path)
    finished = await archive.start(CHARGER_A, start_payload(timestamp="2026-10-03T09:00:00Z"))
    await archive.stop(CHARGER_A, stop_payload(finished, timestamp="2026-10-03T09:30:00Z"))
    active = await archive.start(CHARGER_A, start_payload(id_tag="current", timestamp="2026-10-03T10:00:00Z"))

    query = TransactionQuery(tmp_path)

    assert [view.transaction_id for view in query.active()] == [active]
    assert query.last().transaction_id == finished
    assert query.last().transaction_id not in {view.transaction_id for view in query.active()}


@pytest.mark.asyncio
async def test_last_returns_none_when_only_active_transaction_exists(tmp_path):
    archive = TransactionArchive(tmp_path)
    active = await archive.start(CHARGER_A, start_payload())

    query = TransactionQuery(tmp_path)

    assert [view.transaction_id for view in query.active()] == [active]
    assert query.last() is None


@pytest.mark.asyncio
async def test_recovered_unstopped_transaction_is_active_not_last(tmp_path):
    archive = TransactionArchive(tmp_path)
    finished = await archive.start(CHARGER_A, start_payload(timestamp="2026-10-03T09:00:00Z"))
    await archive.stop(CHARGER_A, stop_payload(finished, timestamp="2026-10-03T09:30:00Z"))
    await archive.meter_values(
        CHARGER_A,
        {
            "connector_id": 2,
            "transaction_id": 225,
            "meter_value": [{"timestamp": "2026-10-03T11:00:00Z"}],
        },
    )

    query = TransactionQuery(tmp_path)

    assert [view.transaction_id for view in query.active()] == [225]
    assert query.get(225).status == "recovered"
    assert query.last().transaction_id == finished


@pytest.mark.asyncio
async def test_recovered_stopped_transaction_can_be_last(tmp_path):
    archive = TransactionArchive(tmp_path)
    local = await archive.start(CHARGER_A, start_payload(timestamp="2026-10-03T09:00:00Z"))
    await archive.stop(CHARGER_A, stop_payload(local, timestamp="2026-10-03T09:30:00Z"))
    await archive.stop(CHARGER_A, stop_payload(225, timestamp="2026-10-03T11:00:00Z"))

    last = TransactionQuery(tmp_path).last()

    assert last.transaction_id == 225
    assert last.status == "stopped"
    assert last.record["origin"] == "recovered"


@pytest.mark.asyncio
async def test_filters_apply_before_last_selection(tmp_path):
    archive = TransactionArchive(tmp_path)
    a1 = await archive.start(
        CHARGER_A,
        start_payload(connector=1, id_tag="alpha", timestamp="2026-10-03T08:00:00Z"),
    )
    await archive.stop(CHARGER_A, stop_payload(a1, timestamp="2026-10-03T08:30:00Z"))
    b1 = await archive.start(
        CHARGER_B,
        start_payload(connector=2, id_tag="beta", timestamp="2026-10-03T09:00:00Z"),
    )
    await archive.stop(CHARGER_B, stop_payload(b1, timestamp="2026-10-03T09:30:00Z"))
    a2 = await archive.start(
        CHARGER_A,
        start_payload(connector=2, id_tag="alpha", timestamp="2026-10-03T10:00:00Z"),
    )
    await archive.stop(CHARGER_A, stop_payload(a2, timestamp="2026-10-03T10:30:00Z"))

    query = TransactionQuery(tmp_path)

    assert query.last(charger=CHARGER_A).transaction_id == a2
    assert query.last(charger=CHARGER_A, connector=1).transaction_id == a1
    assert query.last(id_tag="beta").transaction_id == b1


@pytest.mark.asyncio
async def test_list_filters_time_and_limit(tmp_path):
    archive = TransactionArchive(tmp_path)
    ids = []
    for hour in (8, 9, 10):
        transaction_id = await archive.start(
            CHARGER_A,
            start_payload(timestamp=f"2026-10-03T{hour:02d}:00:00Z", id_tag=f"card-{hour}"),
        )
        await archive.stop(
            CHARGER_A,
            stop_payload(transaction_id, timestamp=f"2026-10-03T{hour:02d}:30:00Z"),
        )
        ids.append(transaction_id)

    query = TransactionQuery(tmp_path)
    matches = query.list(
        since="2026-10-03T08:45:00Z",
        until=datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc),
        limit=1,
    )

    assert [view.transaction_id for view in matches] == [ids[1]]


@pytest.mark.asyncio
async def test_connector_filter_uses_recovered_meter_evidence(tmp_path):
    archive = TransactionArchive(tmp_path)
    await archive.meter_values(
        CHARGER_A,
        {
            "connector_id": 4,
            "transaction_id": 225,
            "meter_value": [{"timestamp": "2026-10-03T11:00:00Z"}],
        },
    )

    matches = TransactionQuery(tmp_path).list(connector=4)

    assert [view.transaction_id for view in matches] == [225]
    assert matches[0].connector_id == 4


@pytest.mark.asyncio
async def test_unresolved_collision_evidence_is_attached_to_view(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start(CHARGER_A, start_payload(timestamp="2026-10-03T10:00:00Z"))
    await archive.stop(
        CHARGER_A,
        stop_payload(transaction_id, timestamp="2026-10-03T09:00:00Z"),
    )

    view = TransactionQuery(tmp_path).get(transaction_id)

    assert view.status == "open"
    assert len(view.unresolved) == 1
    assert view.unresolved[0]["transaction_id"] == transaction_id
    assert view.unresolved[0]["reason"] == "message_predates_local_start"


def test_query_skips_malformed_archive_files(tmp_path):
    directory = tmp_path / "transactions" / "2026-10-03"
    directory.mkdir(parents=True)
    (directory / "broken.json").write_text("not-json", encoding="utf-8")
    (directory / "missing-id.json").write_text(json.dumps({"status": "open"}), encoding="utf-8")

    assert TransactionQuery(tmp_path).list() == []


def test_invalid_time_filter_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Invalid timestamp"):
        TransactionQuery(tmp_path).list(since="not-a-time")
