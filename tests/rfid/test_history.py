import sqlite3

from ocpp_csms.rfid.list_query import RFIDListQuery
from tests.rfid.helpers import record_list

from ocpp_csms.evidence.store import EventStore


def test_record_rfid_list_persists_accepted_snapshot(tmp_path):
    store = EventStore(tmp_path)

    list_id = store.record_rfid_list(
        "charger-a",
        list_version=4,
        entries=[
            {"rfid": "CARD-A", "name": "Alice", "enabled": True},
            {"rfid": "CARD-B", "name": None, "enabled": True},
        ],
        source_file="rfid.csv",
        list_hash="abc123",
        verified_version=4,
    )

    assert store.latest_rfid_list_version("charger-a") == 4
    with sqlite3.connect(store.path) as connection:
        header = connection.execute(
            """
            SELECT charger_id, list_version, source_file, list_hash, verified_version
            FROM rfid_lists WHERE id = ?
            """,
            (list_id,),
        ).fetchone()
        entries = connection.execute(
            """
            SELECT rfid, name, enabled
            FROM rfid_list_entries WHERE list_id = ? ORDER BY rfid
            """,
            (list_id,),
        ).fetchall()

    assert header == ("charger-a", 4, "rfid.csv", "abc123", 4)
    assert entries == [("CARD-A", "Alice", 1), ("CARD-B", None, 1)]


def test_rfid_list_versions_are_scoped_per_charger(tmp_path):
    store = EventStore(tmp_path)
    store.record_rfid_list(
        "charger-a",
        list_version=7,
        entries=[],
        source_file=None,
        list_hash="empty",
        verified_version=0,
    )
    store.record_rfid_list(
        "charger-b",
        list_version=2,
        entries=[],
        source_file=None,
        list_hash="empty",
        verified_version=0,
    )

    assert store.latest_rfid_list_version("charger-a") == 7
    assert store.latest_rfid_list_version("charger-b") == 2
    assert store.latest_rfid_list_version("charger-c") is None


def test_missing_database_has_no_rfid_history(tmp_path):
    query = RFIDListQuery(tmp_path)

    assert query.list() == []
    assert query.latest("charger-a") is None
    assert query.version("charger-a", 1) is None
    assert query.has_history("charger-a") is False
    assert not query.path.exists()


def test_query_lists_accepted_snapshots_with_entries(tmp_path):
    store = EventStore(tmp_path)
    record_list(store, "charger-a", 4, name="Alice")
    record_list(store, "charger-b", 2, rfid="CARD-B", name="Bob")

    snapshots = RFIDListQuery(tmp_path).list()

    assert len(snapshots) == 2
    by_charger = {snapshot.charger_id: snapshot for snapshot in snapshots}
    assert by_charger["charger-a"].list_version == 4
    assert by_charger["charger-a"].verified_version == 4
    assert by_charger["charger-a"].source_file == "rfid.csv"
    assert [(entry.rfid, entry.name, entry.enabled) for entry in by_charger["charger-a"].entries] == [
        ("CARD-A", "Alice", True)
    ]
    assert by_charger["charger-b"].entries[0].rfid == "CARD-B"


def test_query_scopes_history_by_charger_and_version(tmp_path):
    store = EventStore(tmp_path)
    record_list(store, "charger-a", 3, rfid="OLD")
    record_list(store, "charger-a", 4, rfid="CURRENT")
    record_list(store, "charger-b", 4, rfid="OTHER")

    query = RFIDListQuery(tmp_path)

    assert query.has_history("charger-a") is True
    assert query.has_history("charger-c") is False
    assert {snapshot.list_version for snapshot in query.list(charger="charger-a")} == {3, 4}
    assert query.version("charger-a", 4).entries[0].rfid == "CURRENT"
    assert query.version("charger-b", 4).entries[0].rfid == "OTHER"
    assert query.version("charger-a", 99) is None


def test_reused_version_returns_most_recent_snapshot(tmp_path, monkeypatch):
    store = EventStore(tmp_path)
    times = iter(("2026-10-07T01:00:00Z", "2026-10-07T02:00:00Z"))
    monkeypatch.setattr("ocpp_csms.evidence.store.utc_now_iso", lambda: next(times))
    record_list(store, "charger-a", 0, rfid="FIRST", source=None)
    record_list(store, "charger-a", 0, rfid="SECOND", source=None)

    snapshot = RFIDListQuery(tmp_path).version("charger-a", 0)

    assert snapshot is not None
    assert snapshot.entries[0].rfid == "SECOND"
    assert RFIDListQuery(tmp_path).latest("charger-a").id == snapshot.id
