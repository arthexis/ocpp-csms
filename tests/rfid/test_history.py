import sqlite3

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
