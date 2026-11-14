"""Hermetic Stage 4C parity for diagnostic event and transaction retrieval."""
from __future__ import annotations

import json
import sqlite3
import pytest

import ocpp_csms.evidence.diagnostics as diagnostics
from ocpp_csms.evidence import _diagnostics_queries
from ocpp_csms.evidence.store import DATABASE_FILENAME, EventStore
from ocpp_csms.evidence.contracts import events_contract, raw_events_contract
from ocpp_csms.cli import build_parser, diagnostics as cli

EVENT_SQL = "\n            SELECT * FROM (\n                SELECT id, received_at AS occurred_at, charger_id, 'ocpp' AS kind,\n                       action, direction, transaction_id, id_tag, payload_json AS payload\n                FROM events\n                UNION ALL\n                SELECT id, occurred_at, charger_id, 'runtime', event, NULL, NULL, NULL, details_json\n                FROM runtime_events\n            )\n            WHERE (? IS NULL OR charger_id = ?)\n              {tx_condition}\n              AND (? IS NULL OR occurred_at >= ?)\n              AND (? IS NULL OR occurred_at <= ?)\n              {action_where}\n            ORDER BY occurred_at DESC, id DESC\n            LIMIT ?\n            "
TRANSACTION_SQL = "\n            SELECT id, received_at AS occurred_at, charger_id, 'ocpp' AS kind,\n                   action, direction, transaction_id, id_tag, payload_json AS payload\n            FROM events\n            WHERE transaction_id = ?\n            ORDER BY occurred_at ASC, id ASC\n            LIMIT ?\n            "


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class RecordingDB:
    row_factory = None

    def __init__(self, rows=None, error=None):
        self.rows = [] if rows is None else rows
        self.error = error
        self.calls = []
        self.closed = False

    def execute(self, query, params):
        self.calls.append((query, tuple(params)))
        if self.error is not None:
            raise self.error
        return Cursor(self.rows)

    def close(self):
        self.closed = True


def connect_fake(tmp_path, monkeypatch, db):
    path = tmp_path / DATABASE_FILENAME
    path.touch()
    opened = []
    monkeypatch.setattr(diagnostics.sqlite3, "connect",
                        lambda target: opened.append(target) or db)
    return opened


@pytest.mark.parametrize(("options", "tx_filter", "action_filter", "params"), [
    ({}, "", "", (None, None, None, None, None, None, 200)),
    ({"charger_id": "CP-A", "transaction_id": 42, "limit": 3},
     "AND transaction_id IN (?)", "",
     ("CP-A", "CP-A", 42, None, None, None, None, 3)),
    ({"transaction_id": (7, 8), "since": "2026-11-13T04:00:00-06:00",
      "until": "2026-11-13T10:00:00Z", "only": ("heartbeat", "boot"),
      "exclude": ("fault",), "limit": None},
     "AND transaction_id IN (?,?)",
     " AND (instr(lower(action), ?) > 0 OR instr(lower(action), ?) > 0)"
     " AND instr(lower(action), ?) = 0",
     (None, None, 7, 8, "2026-11-13T10:00:00Z", "2026-11-13T10:00:00Z",
      "2026-11-13T10:00:00Z", "2026-11-13T10:00:00Z",
      "heartbeat", "boot", "fault", -1)),
    ({"transaction_id": (), "exclude": ("a", "b"), "limit": 0},
     "AND transaction_id IN ()",
     " AND instr(lower(action), ?) = 0 AND instr(lower(action), ?) = 0",
     (None, None, None, None, None, None, "a", "b", 0)),
])
def test_events_sql_exact_and_filter_parameter_order(tmp_path, monkeypatch, options,
                                                      tx_filter, action_filter, params):
    db = RecordingDB([{"id": 3}, {"id": 2}, {"id": 1}])
    opened = connect_fake(tmp_path, monkeypatch, db)
    assert diagnostics.events_between(tmp_path, **options) == [
        {"id": 1}, {"id": 2}, {"id": 3},
    ]
    assert db.calls == [(EVENT_SQL.format(tx_condition=tx_filter,
                                          action_where=action_filter), params)]
    assert opened == [tmp_path / DATABASE_FILENAME]
    assert db.row_factory is sqlite3.Row and db.closed


@pytest.mark.parametrize("limit", [1, 0, -1, 1000])
def test_transaction_sql_exact_int_cast_and_no_reverse(tmp_path, monkeypatch, limit):
    rows = [{"id": 1}, {"id": 2}]
    db = RecordingDB(rows)
    opened = connect_fake(tmp_path, monkeypatch, db)
    assert diagnostics.transaction_events(tmp_path, "42", limit=limit) is rows
    assert db.calls == [(TRANSACTION_SQL, (42, limit))]
    assert opened == [tmp_path / DATABASE_FILENAME]
    assert db.row_factory is sqlite3.Row and db.closed


def test_absent_database_returns_empty_without_connect(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics.sqlite3, "connect",
                        lambda *a: pytest.fail("unexpected SQLite connection"))
    assert diagnostics.events_between(tmp_path) == []
    assert diagnostics.transaction_events(tmp_path, 42) == []


def test_invalid_or_reversed_time_precedes_file_and_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics.sqlite3, "connect",
                        lambda *a: pytest.fail("invalid time must not connect"))
    with pytest.raises(ValueError, match="--since must be earlier"):
        diagnostics.events_between(tmp_path, since="2026-10-01T16:00:00Z",
                                   until="2026-10-01T15:00:00Z")
    with pytest.raises(ValueError):
        diagnostics.events_between(tmp_path, since="not-a-date")


@pytest.mark.parametrize("which", ["events", "transaction"])
def test_sql_error_closes_connection_and_propagates(tmp_path, monkeypatch, which):
    db = RecordingDB(error=sqlite3.OperationalError("synthetic read failure"))
    connect_fake(tmp_path, monkeypatch, db)
    with pytest.raises(sqlite3.OperationalError, match="synthetic read failure"):
        if which == "events":
            diagnostics.events_between(tmp_path, only=("heartbeat",))
        else:
            diagnostics.transaction_events(tmp_path, 42)
    assert len(db.calls) == 1 and db.closed


def test_invalid_transaction_id_is_converted_inside_finally(tmp_path, monkeypatch):
    class InvalidID:
        def __int__(self):
            raise ValueError("conversion inside try")

    db = RecordingDB()
    connect_fake(tmp_path, monkeypatch, db)
    with pytest.raises(ValueError, match="conversion inside try"):
        diagnostics.transaction_events(tmp_path, InvalidID())
    assert db.calls == [] and db.closed


def test_filter_construction_stays_after_open_before_execute(tmp_path, monkeypatch):
    db = RecordingDB()
    opened = connect_fake(tmp_path, monkeypatch, db)
    with pytest.raises(TypeError):
        diagnostics.events_between(tmp_path, only=7)
    assert len(opened) == 1 and db.calls == []


def test_private_query_monkeypatch_is_dynamic(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(_diagnostics_queries, "select_events_between",
                        lambda db, **kwargs: calls.append(("events", kwargs)) or [{"id": 2}, {"id": 1}])
    monkeypatch.setattr(_diagnostics_queries, "select_transaction_events",
                        lambda db, tx, limit: calls.append(("txn", tx, limit)) or [{"id": 3}])
    db = RecordingDB()
    connect_fake(tmp_path, monkeypatch, db)
    assert diagnostics.events_between(tmp_path, limit=2) == [{"id": 1}, {"id": 2}]
    assert diagnostics.transaction_events(tmp_path, "42", limit=4) == [{"id": 3}]
    assert calls[0][0] == "events" and calls[0][1]["limit"] == 2
    assert calls[1] == ("txn", 42, 4)
    assert db.closed and db.calls == []


def test_faults_keep_received_timestamp_vendor_fields_and_post_filter_limit(monkeypatch):
    recorded = []
    def event(payload, received, *, kind="ocpp", direction="in"):
        return {"kind": kind, "direction": direction, "action": "StatusNotification",
                "occurred_at": received, "charger_id": "CP-A", "payload": payload}
    selected = [
        event(json.dumps({"status": "Faulted"}), "2026-11-13T10:00:01Z"),
        event("{bad", "2026-11-13T10:00:02Z"),
        event(json.dumps({"status": "Available"}), "2026-11-13T10:00:03Z"),
        event(json.dumps({"status": "Faulted"}), "2026-11-13T10:00:04Z", kind="runtime"),
        event(json.dumps({"status": "Faulted"}), "2026-11-13T10:00:05Z", direction="out"),
        event(json.dumps({"status": "Faulted", "error_code": "InternalError",
                          "timestamp": "2026-11-09T00:00:00Z", "connector_id": 1,
                          "vendor_id": "vendor", "vendor_error_code": "E42"}),
              "2026-11-13T10:00:06Z"),
    ]
    monkeypatch.setattr(diagnostics, "events_between",
                        lambda *args, **kw: recorded.append(kw) or selected)
    rows = diagnostics.fault_events("/unused", charger_id="CP-A", limit=1)
    assert len(rows) == 1 and rows[0]["received_at"] == "2026-11-13T10:00:06Z"
    assert rows[0]["charger_at"] == "2026-11-09T00:00:00Z"
    assert rows[0]["vendor_error_code"] == "E42"
    assert recorded == [{"charger_id": "CP-A", "since": None,
                         "until": None, "limit": None}]


def test_fault_invalid_limit_does_not_retrieve(monkeypatch):
    monkeypatch.setattr(diagnostics, "events_between",
                        lambda *args, **kw: pytest.fail("invalid limit queried"))
    with pytest.raises(ValueError, match="--limit must be positive"):
        diagnostics.fault_events("/unused", limit=0)


def test_real_temporary_sqlite_order_filters_and_transaction_scope(tmp_path):
    store = EventStore(tmp_path)
    store.record_runtime("charger_connected", charger_id="CP-A")
    store.record_ocpp("CP-A", "Heartbeat", {})
    store.record_ocpp("CP-A", "MeterValues", {"connector_id": 1}, transaction_id=42)
    store.record_ocpp("CP-B", "MeterValues", {"connector_id": 2}, transaction_id=99)
    merged = diagnostics.events_between(tmp_path, charger_id="CP-A", limit=None)
    assert [row["action"] for row in merged] == [
        "charger_connected", "Heartbeat", "MeterValues",
    ]
    assert [row["kind"] for row in merged] == ["runtime", "ocpp", "ocpp"]
    scoped = diagnostics.events_between(tmp_path, transaction_id=(42, 99), limit=2)
    assert {row["transaction_id"] for row in scoped} == {42, 99}
    tx = diagnostics.transaction_events(tmp_path, 42)
    assert len(tx) == 1 and tx[0]["action"] == "MeterValues"
    assert tx[0]["transaction_id"] == 42


def test_events_cli_json_contracts_use_same_rows(monkeypatch, tmp_path):
    event = {"id": 1, "occurred_at": "2026-11-13T10:00:00Z", "charger_id": "CP-A",
             "kind": "ocpp", "action": "Heartbeat", "direction": "in",
             "transaction_id": None, "id_tag": None, "payload": "{}"}
    rows = [event]
    monkeypatch.setattr(cli, "events_between", lambda *args, **kw: rows)
    emitted = []
    monkeypatch.setattr(cli, "emit_json", emitted.append)
    parser = build_parser()[0]
    args = parser.parse_args(["--data-dir", str(tmp_path), "events", "--json"])
    assert cli.run_events(args) == 0 and emitted[-1] == events_contract(rows)
    args.raw = True
    assert cli.run_events(args) == 0 and emitted[-1] == raw_events_contract(rows)
