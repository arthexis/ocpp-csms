"""Offline Stage 4B: exact SQL and interleaved runtime/status query parity."""
from __future__ import annotations

import sqlite3
import pytest

import ocpp_csms.status as status
from ocpp_csms import _status_queries
from ocpp_csms.status_contract import charger_status_data

SQL = (
    "\n        SELECT id, occurred_at, event FROM runtime_events\n        WHERE charger_id = ? AND event IN ('charger_connected', 'charger_disconnected')\n        ORDER BY id DESC LIMIT 1\n        ",
    "\n        SELECT details_json FROM runtime_events\n        WHERE charger_id = ? AND event = 'charger_connected'\n        ORDER BY id DESC LIMIT 1\n        ",
    "\n        SELECT received_at FROM events\n        WHERE charger_id = ? AND direction = 'in'\n        ORDER BY id DESC LIMIT 1\n        ",
    "\n        SELECT connector_id, status, error_code, info, received_at\n        FROM connector_status\n        WHERE charger_id = ?\n        ORDER BY connector_id\n        ",
    "\n        SELECT transaction_id, connector_id, id_tag, started_at, start_received_at\n        FROM transactions\n        WHERE charger_id = ? AND state = 'open'\n        ORDER BY start_received_at DESC\n        ",
)
HELPERS = (
    "latest_runtime_event", "latest_connection_metadata", "latest_inbound_event",
    "connector_status_rows", "open_transaction_rows",
)


class Cursor:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value

    def fetchall(self):
        return self.value


class RecordedDB:
    def __init__(self, values):
        self.values = list(values)
        self.calls = []
        self.closed = False

    def execute(self, statement, params):
        n = len(self.calls)
        assert n < len(self.values), "additional/unexpected SQL query"
        self.calls.append((statement, params))
        return Cursor(self.values[n])

    def close(self):
        self.closed = True


def values(event="charger_connected", since_id=12, statuses=None, transactions=None,
           details='{"subprotocol": "ocpp1.6"}'):
    return [
        None if event is None else {"id": since_id, "event": event, "occurred_at": "2026-11-13T10:00:00Z"},
        {"details_json": details},
        {"received_at": "2026-11-13T10:01:00Z"},
        [] if statuses is None else statuses,
        [] if transactions is None else transactions,
    ]


def physical(cid, state, error="NoError", info=None):
    return {"connector_id": cid, "status": state, "error_code": error, "info": info,
            "received_at": "2026-11-13T10:01:00Z"}


def history():
    return {"transaction_id": 420, "connector_id": 1, "id_tag": "tag-a",
            "started_at": None, "start_received_at": "2026-11-13T09:58:00Z"}


@pytest.mark.parametrize("index", range(5))
def test_each_query_sql_params_and_return_mode_match_original(index):
    expected = {"n": index} if index < 3 else [{"n": index}]
    db = RecordedDB([expected])
    assert getattr(_status_queries, HELPERS[index])(db, "CP-1") is expected
    assert db.calls == [(SQL[index], ("CP-1",))]
    assert not db.closed


def test_five_queries_are_interleaved_without_new_connections(monkeypatch):
    monkeypatch.setattr(status, "_connect", lambda *a: pytest.fail("new connection"))
    db = RecordedDB(values(
        statuses=[physical(0, "Available"), physical(1, "Charging"),
                  physical(2, "Faulted", "InternalError", "EmergencyStop")],
        transactions=[history()],
    ))
    charger = status._charger_status(db, "CP-1", live_since_id=11)
    assert db.calls == [(statement, ("CP-1",)) for statement in SQL]
    assert charger.connected and charger.subprotocol == "ocpp1.6"
    assert charger.last_seen == "2026-11-13T10:01:00Z"
    assert (charger.status, charger.derived_status) == ("Faulted", "EmergencyStop")
    assert (charger.availability, charger.activity) == ("unavailable", "active")
    assert charger.transaction_id == 420
    assert [c.connector_id for c in charger.connectors] == [0, 1, 2]
    assert charger_status_data(charger)["derived_status"] == "EmergencyStop"
    assert not db.closed


@pytest.mark.parametrize(("statuses", "availability", "activity", "derived"), [
    ([physical(1, "Available")], "available", "inactive", "Available"),
    ([], "unknown", "unknown", None),
])
def test_old_open_transaction_cannot_fabricate_observed_charging(
    statuses, availability, activity, derived
):
    db = RecordedDB(values(statuses=statuses, transactions=[history()]))
    charger = status._charger_status(db, "CP-1")
    assert (charger.availability, charger.activity, charger.derived_status) == (
        availability, activity, derived,
    )
    assert charger.transaction_id == 420
    assert db.calls == [(statement, ("CP-1",)) for statement in SQL]


@pytest.mark.parametrize(("event", "boundary", "connected"), [
    ("charger_connected", None, True),
    ("charger_connected", 11, True),
    ("charger_connected", 12, False),
    ("charger_disconnected", 11, False),
    (None, 11, False),
])
def test_live_since_id_and_connection_generation(event, boundary, connected):
    db = RecordedDB(values(event=event))
    charger = status._charger_status(db, "CP-1", live_since_id=boundary)
    assert charger.connected is connected
    assert charger.derived_status == ("Available" if connected else None)
    assert charger.activity == ("inactive" if connected else "unknown")
    assert db.calls == [(statement, ("CP-1",)) for statement in SQL]


def test_bad_json_does_not_interrupt_later_sql_reads():
    db = RecordedDB(values(details="{malformed"))
    assert status._charger_status(db, "CP-1").subprotocol is None
    assert db.calls == [(statement, ("CP-1",)) for statement in SQL]


def test_first_sql_error_propagates_without_read_ahead():
    class Failure(RecordedDB):
        def execute(self, statement, params):
            self.calls.append((statement, params))
            raise sqlite3.OperationalError("synthetic failure")

    db = Failure([])
    with pytest.raises(sqlite3.OperationalError, match="synthetic failure"):
        status._charger_status(db, "CP-1")
    assert db.calls == [(SQL[0], ("CP-1",))]


def test_connected_decision_precedes_second_query():
    class Broken(dict):
        def __getitem__(self, key):
            if key == "event":
                raise LookupError("runtime first")
            return super().__getitem__(key)

    db = RecordedDB([Broken({"event": "charger_connected", "id": 12})])
    with pytest.raises(LookupError, match="runtime first"):
        status._charger_status(db, "CP-1")
    assert db.calls == [(SQL[0], ("CP-1",))]


def test_metadata_decision_precedes_third_query():
    class Broken(dict):
        def __getitem__(self, key):
            raise LookupError("metadata first")

    db = RecordedDB([{"event": "charger_connected", "id": 12, "occurred_at": "now"},
                     Broken({"details_json": "{}"})])
    with pytest.raises(LookupError, match="metadata first"):
        status._charger_status(db, "CP-1")
    assert db.calls == [(statement, ("CP-1",)) for statement in SQL[:2]]


def test_optional_connection_retains_ownership(tmp_path, monkeypatch):
    path = tmp_path / "existing.sqlite"
    path.touch()
    db = RecordedDB(values(statuses=[physical(1, "Available")]))
    monkeypatch.setattr(status, "_connect", lambda *a: pytest.fail("connection replaced"))
    chargers = status.charger_statuses(path, charger_id="CP-1", connection=db)
    assert len(chargers) == 1 and chargers[0].status == "Available"
    assert db.calls == [(statement, ("CP-1",)) for statement in SQL]
    assert not db.closed


def test_original_connect_row_factory_and_patch_seam(tmp_path, monkeypatch):
    class FakeDB:
        row_factory = None

    db = FakeDB()
    calls = []
    monkeypatch.setattr(status.sqlite3, "connect", lambda path: calls.append(path) or db)
    path = tmp_path / "not-created.sqlite"
    assert status._connect(path) is db
    assert db.row_factory is sqlite3.Row
    assert calls == [path]


def test_dynamic_query_helper_monkeypatch(monkeypatch):
    original = _status_queries.latest_runtime_event
    invoked = []
    monkeypatch.setattr(_status_queries, "latest_runtime_event",
                        lambda db, cp: invoked.append(cp) or original(db, cp))
    db = RecordedDB(values())
    status._charger_status(db, "CP-1")
    assert invoked == ["CP-1"]
    assert len(db.calls) == 5
