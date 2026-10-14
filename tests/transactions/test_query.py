import json
from datetime import datetime, timezone

import pytest

from ocpp_csms.transactions.query import TransactionQuery
from ocpp_csms.transactions.archive import TransactionArchive
from ocpp_csms.evidence.store import EventStore


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


def test_lists_newest_first_and_gets_one_transaction(tmp_path):
    store = EventStore(tmp_path)
    first = store.commit_start(CHARGER_A, start_payload(timestamp="2026-10-03T10:00:00Z"))
    store.commit_transaction_evidence(CHARGER_A, "StopTransaction", stop_payload(first, timestamp="2026-10-03T10:10:00Z"))
    second = store.commit_start(CHARGER_A, start_payload(id_tag="card-b", timestamp="2026-10-03T11:00:00Z"))

    query = TransactionQuery(tmp_path)

    assert [view.transaction_id for view in query.list()] == [second, first]
    assert query.get(first).status == "stopped"
    assert query.get(9999) is None


def test_last_includes_newest_active_transaction(tmp_path):
    store = EventStore(tmp_path)
    finished = store.commit_start(CHARGER_A, start_payload(timestamp="2026-10-03T09:00:00Z"))
    store.commit_transaction_evidence(CHARGER_A, "StopTransaction", stop_payload(finished, timestamp="2026-10-03T09:30:00Z"))
    active = store.commit_start(CHARGER_A, start_payload(id_tag="current", timestamp="2026-10-03T10:00:00Z"))

    query = TransactionQuery(tmp_path)

    assert [view.transaction_id for view in query.active()] == [active]
    assert query.last().transaction_id == active
    assert query.last().transaction_id in {view.transaction_id for view in query.active()}


def test_last_returns_active_when_only_active_transaction_exists(tmp_path):
    store = EventStore(tmp_path)
    active = store.commit_start(CHARGER_A, start_payload())

    query = TransactionQuery(tmp_path)

    assert [view.transaction_id for view in query.active()] == [active]
    assert query.last().transaction_id == active


def test_filters_apply_before_last_selection(tmp_path):
    store = EventStore(tmp_path)
    a1 = store.commit_start(
        CHARGER_A,
        start_payload(connector=1, id_tag="alpha", timestamp="2026-10-03T08:00:00Z"),
    )
    store.commit_transaction_evidence(CHARGER_A, "StopTransaction", stop_payload(a1, timestamp="2026-10-03T08:30:00Z"))
    b1 = store.commit_start(
        CHARGER_B,
        start_payload(connector=2, id_tag="beta", timestamp="2026-10-03T09:00:00Z"),
    )
    store.commit_transaction_evidence(CHARGER_B, "StopTransaction", stop_payload(b1, timestamp="2026-10-03T09:30:00Z"))
    a2 = store.commit_start(
        CHARGER_A,
        start_payload(connector=2, id_tag="alpha", timestamp="2026-10-03T10:00:00Z"),
    )
    store.commit_transaction_evidence(CHARGER_A, "StopTransaction", stop_payload(a2, timestamp="2026-10-03T10:30:00Z"))

    query = TransactionQuery(tmp_path)

    assert query.last(charger=CHARGER_A).transaction_id == a2
    assert query.last(charger=CHARGER_A, connector=1).transaction_id == a1
    assert query.last(id_tag="beta").transaction_id == b1


def test_list_filters_time_and_limit(tmp_path):
    store = EventStore(tmp_path)
    ids = []
    for hour in (8, 9, 10):
        transaction_id = store.commit_start(
            CHARGER_A,
            start_payload(timestamp=f"2026-10-03T{hour:02d}:00:00Z", id_tag=f"card-{hour}"),
        )
        store.commit_transaction_evidence(CHARGER_A, "StopTransaction", stop_payload(transaction_id, timestamp=f"2026-10-03T{hour:02d}:30:00Z"),
        )
        ids.append(transaction_id)

    query = TransactionQuery(tmp_path)
    matches = query.list(
        since="2026-10-03T08:45:00Z",
        until=datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc),
        limit=1,
    )

    assert [view.transaction_id for view in matches] == [ids[1]]


def test_query_skips_malformed_archive_files(tmp_path):
    directory = tmp_path / "transactions" / "2026-10-03"
    directory.mkdir(parents=True)
    (directory / "broken.json").write_text("not-json", encoding="utf-8")
    (directory / "missing-id.json").write_text(json.dumps({"status": "open"}), encoding="utf-8")

    assert TransactionQuery(tmp_path).list() == []


def test_invalid_time_filter_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Invalid timestamp"):
        TransactionQuery(tmp_path).list(since="not-a-time")
