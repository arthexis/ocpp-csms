from ocpp_csms.events import EventStore
from ocpp_csms.rfid_list_query import RFIDListQuery


def record(
    store,
    charger,
    version,
    *,
    name=None,
    rfid="CARD-A",
    verified=None,
    source="rfid.csv",
):
    return store.record_rfid_list(
        charger,
        list_version=version,
        entries=[{"rfid": rfid, "name": name, "enabled": True}],
        source_file=source,
        list_hash=f"hash-{charger}-{version}-{rfid}",
        verified_version=version if verified is None else verified,
    )


def test_missing_database_has_no_rfid_history(tmp_path):
    query = RFIDListQuery(tmp_path)

    assert query.list() == []
    assert query.latest("charger-a") is None
    assert query.version("charger-a", 1) is None
    assert query.has_history("charger-a") is False
    assert not tmp_path.exists()


def test_query_lists_accepted_snapshots_with_entries(tmp_path):
    store = EventStore(tmp_path)
    record(store, "charger-a", 4, name="Alice")
    record(store, "charger-b", 2, rfid="CARD-B", name="Bob")

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
    record(store, "charger-a", 3, rfid="OLD")
    record(store, "charger-a", 4, rfid="CURRENT")
    record(store, "charger-b", 4, rfid="OTHER")

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
    monkeypatch.setattr("ocpp_csms.events.utc_now_iso", lambda: next(times))
    record(store, "charger-a", 0, rfid="FIRST", source=None)
    record(store, "charger-a", 0, rfid="SECOND", source=None)

    snapshot = RFIDListQuery(tmp_path).version("charger-a", 0)

    assert snapshot is not None
    assert snapshot.entries[0].rfid == "SECOND"
    assert RFIDListQuery(tmp_path).latest("charger-a").id == snapshot.id
