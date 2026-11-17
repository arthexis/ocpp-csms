"""Stage 5: deterministic SQLite read amplification, not a latency budget."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from math import ceil
from time import perf_counter

import pytest

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.schema import CURRENT_SCHEMA_VERSION, database_path
from ocpp_csms.transactions.query import TransactionQuery


class _CountingCursor:
    def __init__(self, cursor, counts, category):
        self._cursor = cursor
        self._counts = counts
        self._category = category

    def fetchall(self):
        rows = self._cursor.fetchall()
        self._counts.append((self._category, len(rows)))
        return rows

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _CountingConnection:
    """Observe fetch cardinality without changing SQL or SQLite ownership."""
    def __init__(self, underlying):
        self.underlying = underlying
        self.counts = []

    @property
    def row_factory(self):
        return self.underlying.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self.underlying.row_factory = value

    def execute(self, sql, params=()):
        cursor = self.underlying.execute(sql, params)
        if sql.startswith("SELECT * FROM transaction_summary"):
            return _CountingCursor(cursor, self.counts, "summaries")
        if "FROM events WHERE direction = 'in'" in sql:
            return _CountingCursor(cursor, self.counts, "evidence")
        return cursor


def _seed(data_dir, total):
    EventStore(data_dir)
    base = datetime(2026, 11, 16, 16, 0, tzinfo=timezone.utc)
    transactions = []
    events = []
    for number in range(1, total + 1):
        moment = (base + timedelta(seconds=number)).isoformat().replace("+00:00", "Z")
        meter_time = (base + timedelta(seconds=number, milliseconds=100)).isoformat().replace("+00:00", "Z")
        charger = "CP-A" if number % 2 else "CP-B"
        transactions.append((number, charger, 1, "CARD", 100, moment, moment, moment))
        start = {"connector_id": 1, "id_tag": "CARD", "meter_start": 100, "timestamp": moment}
        meter = {"connector_id": 1, "transaction_id": number, "meter_value": [
            {"timestamp": meter_time, "sampled_value": [
                {"measurand": "Energy.Active.Import.Register", "unit": "Wh",
                 "value": str(100 + number)}
            ]}
        ]}
        events.extend([
            (moment, charger, "StartTransaction", "in", number, json.dumps(start)),
            (moment, charger, "MeterValues", "in", number, json.dumps(meter)),
        ])
    with sqlite3.connect(database_path(data_dir)) as connection:
        connection.executemany(
            """INSERT INTO transactions
            (transaction_id,charger_id,connector_id,id_tag,meter_start,started_at,
             start_received_at,state,last_activity_at)
            VALUES (?,?,?,?,?,?,?,'open',?)""", transactions
        )
        connection.executemany(
            """INSERT INTO events
            (received_at,charger_id,action,direction,transaction_id,payload_json)
            VALUES (?,?,?,?,?,?)""", events
        )


@pytest.mark.parametrize("total", [24, 405])
def test_list_limit_and_last_read_same_full_snapshot(tmp_path, total, record_property):
    """Document current full reconstruction before sorting/limit (no perf threshold)."""
    _seed(tmp_path, total)
    with sqlite3.connect(database_path(tmp_path)) as real:
        assert real.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        proxy = _CountingConnection(real)
        query = TransactionQuery(tmp_path, connection=proxy)
        for operation in ("list", "last"):
            proxy.counts.clear()
            start = perf_counter()
            result = query.list(limit=1) if operation == "list" else query.last()
            elapsed_ms = (perf_counter() - start) * 1000
            view = result[0] if operation == "list" else result
            assert view is not None and view.transaction_id == total
            assert sum(n for kind, n in proxy.counts if kind == "summaries") == total
            assert sum(n for kind, n in proxy.counts if kind == "evidence") == total * 2
            assert len([kind for kind, _ in proxy.counts if kind == "evidence"]) == ceil(total / 400)
            assert len([kind for kind, _ in proxy.counts if kind == "summaries"]) == 1
            assert real.execute("SELECT 1").fetchone()[0] == 1  # caller retains connection
            record_property(f"{operation}_{total}_elapsed_ms_observed", round(elapsed_ms, 3))
            record_property(f"{operation}_{total}_evidence_rows", total * 2)

        # Preserve physical event identity and snapshot ownership under filters.
        assert query.get(total, charger="CP-A" if total % 2 else "CP-B").transaction_id == total
        assert query.list(charger="CP-A", limit=0) == []
        assert not real.in_transaction  # supplied connection is not closed/committed


def test_receive_time_order_is_independent_of_charger_clock(tmp_path):
    """Two distinct clocks must not be made interchangeable in a SQL refactor."""
    store = EventStore(tmp_path)
    with sqlite3.connect(database_path(tmp_path)) as conn:
        for tx, charger_time, receipt in (
            (1, "2030-01-01T00:00:00Z", "2026-11-16T16:00:00Z"),
            (2, "2020-01-01T00:00:00Z", "2026-11-16T17:00:00Z"),
        ):
            conn.execute(
                """INSERT INTO transactions
                (transaction_id,charger_id,connector_id,id_tag,meter_start,started_at,
                 start_received_at,state,last_activity_at)
                VALUES (?,'CP-A',1,'CARD',0,?,?,'open',?)""",
                (tx, charger_time, receipt, receipt),
            )
            conn.execute(
                """INSERT INTO events
                (received_at,charger_id,action,direction,transaction_id,payload_json)
                VALUES (?,'CP-A','StartTransaction','in',?,?)""",
                (receipt, tx, json.dumps({"connector_id": 1, "id_tag": "CARD",
                                          "meter_start": 0, "timestamp": charger_time})),
            )
    query = TransactionQuery(tmp_path)
    assert [v.transaction_id for v in query.list()] == [1, 2]
    assert [v.transaction_id for v in query.list(local_time=True)] == [2, 1]
    assert query.last(local_time=True).transaction_id == 2
    assert [v.transaction_id for v in query.list(
        local_time=True, since="2026-11-16T11:00:00-06:00"
    )] == [2]
