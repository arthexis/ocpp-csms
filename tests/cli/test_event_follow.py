"""Incremental event follower: independent cursor semantics and JSONL output."""
from __future__ import annotations
import json
import sqlite3

import pytest
from types import SimpleNamespace

from ocpp_csms.cli.event_follow import _cursor, _new_events, _emit
from ocpp_csms.schema import DATABASE_FILENAME, create_current_schema


def _prepare(tmp_path):
    create_current_schema(tmp_path)
    return tmp_path / DATABASE_FILENAME


def write(*, kind, when, charger="CP1", action="Heartbeat", tx=None):
    with sqlite3.connect(db) as con:
        if kind == "ocpp":
            con.execute("""INSERT INTO events
                (received_at, charger_id, action, direction, transaction_id, payload_json)
                VALUES (?, ?, ?, 'in', ?, ?)""",
                (when, charger, action, tx, "{}"))
        else:
            con.execute("""INSERT INTO runtime_events
                (occurred_at, charger_id, event, details_json)
                VALUES (?, ?, ?, ?)""",
                (when, charger, action, "{}"))


@pytest.fixture
def event_feed(tmp_path):
    """Isolated persisted event cursor and writer for incremental follow tests."""
    db = _prepare(tmp_path)
    def write(**kwargs):
        _write(db, **kwargs)
    return tmp_path, write


def test_follow_tracks_both_tables_and_does_not_replay(event_feed):
    tmp_path, write = event_feed
    before = _cursor(tmp_path)
    write(kind="ocpp", when="2026-10-10T22:00:00Z")
    write(kind="runtime", when="2026-10-10T22:00:00Z", action="charger_connected")
    first = _new_events(tmp_path, before)
    assert len(first) == 2
    assert {row["kind"] for row in first} == {"ocpp", "runtime"}
    assert _new_events(tmp_path, before) == []
    write(kind="ocpp", when="2026-10-10T22:00:01Z")
    assert len(_new_events(tmp_path, before)) == 1


def test_filtered_events_still_advance_cursor(event_feed):
    tmp_path, write = event_feed
    cursor = _cursor(tmp_path)
    write(kind="ocpp", when="2026-10-10T22:00:00Z", charger="CP2")
    assert _new_events(tmp_path, cursor, charger_id="CP1") == []
    write(kind="ocpp", when="2026-10-10T22:00:01Z", charger="CP1")
    assert len(_new_events(tmp_path, cursor, charger_id="CP1")) == 1


def test_batch_cursor_does_not_skip_ids_with_out_of_order_times(event_feed):
    tmp_path, write = event_feed
    cursor = _cursor(tmp_path)
    write(kind="ocpp", when="2026-10-10T22:00:09Z")
    write(kind="ocpp", when="2026-10-10T22:00:00Z")
    assert len(_new_events(tmp_path, cursor, batch_size=1)) == 1
    assert len(_new_events(tmp_path, cursor, batch_size=1)) == 1


def test_follow_jsonl_prints_one_event_per_line(event_feed, capsys):
    tmp_path, write = event_feed
    cursor = _cursor(tmp_path)
    write(kind="ocpp", when="2026-10-10T22:00:00Z")
    rows = _new_events(tmp_path, cursor)
    args = SimpleNamespace(json=True, raw=False, verbose=False)
    _emit(args, rows)
    output = capsys.readouterr().out.strip().splitlines()
    assert len(output) == 1
    assert json.loads(output[0])["action"] == "Heartbeat"
