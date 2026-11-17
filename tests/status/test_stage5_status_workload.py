"""Stage 5: count current N+1 status reads without imposing latency budgets."""
from __future__ import annotations

import sqlite3
from time import perf_counter

import pytest

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.schema import database_path
from ocpp_csms.status import charger_statuses


@pytest.mark.parametrize("chargers", [1, 12, 24])
def test_status_read_count_scales_five_queries_per_charger(tmp_path, chargers, record_property):
    EventStore(tmp_path)
    with sqlite3.connect(database_path(tmp_path)) as connection:
        connection.executemany(
            "INSERT INTO runtime_events (occurred_at,event,charger_id) VALUES "
            "('2026-11-16T12:00:00Z','charger_connected',?)",
            [(f"CP-{index:03d}",) for index in range(chargers)],
        )

    with sqlite3.connect(database_path(tmp_path)) as connection:
        connection.row_factory = sqlite3.Row
        reads = []
        connection.set_trace_callback(
            lambda sql: reads.append(sql) if sql.lstrip().upper().startswith("SELECT") else None
        )
        began = perf_counter()
        result = charger_statuses(database_path(tmp_path), connection=connection)
        elapsed_ms = (perf_counter() - began) * 1000
        assert [c.charger_id for c in result] == [f"CP-{i:03d}" for i in range(chargers)]
        assert all(c.connected and c.derived_status == "Available" for c in result)
        assert all(not c.connectors and c.transaction_id is None for c in result)
        # The initial UNION and the five status helpers are all read-only.
        assert len(reads) == 1 + 5 * chargers
        assert sum(" UNION SELECT " in sql.replace("\n", " ") for sql in reads) == 1
        assert connection.execute("SELECT 1").fetchone()[0] == 1
        record_property(f"status_{chargers}_chargers_elapsed_ms_observed", round(elapsed_ms, 3))
        record_property(f"status_{chargers}_chargers_sql_reads", len(reads))
