"""Incremental polling of persisted OCPP and runtime event evidence."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from ocpp_csms.evidence.store import DATABASE_FILENAME
from ocpp_csms.evidence.diagnostics import format_events, _time
from ocpp_csms.evidence.contracts import event_record


def _cursor(data_dir):
    path = Path(data_dir).expanduser() / DATABASE_FILENAME
    if not path.exists():
        return {"ocpp": 0, "runtime": 0}
    with sqlite3.connect(path) as connection:
        return {"ocpp": connection.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0],
                "runtime": connection.execute("SELECT COALESCE(MAX(id), 0) FROM runtime_events").fetchone()[0]}


def _new_events(data_dir, cursor, *, charger_id=None, transaction_id=None, since=None, until=None, batch_size=500):
    path = Path(data_dir).expanduser() / DATABASE_FILENAME
    if not path.exists():
        return []
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("""
            SELECT * FROM (
                SELECT id, received_at AS occurred_at, charger_id, 'ocpp' AS kind,
                       action, direction, transaction_id, id_tag, payload_json AS payload
                FROM events WHERE id > ?
                UNION ALL
                SELECT id, occurred_at, charger_id, 'runtime' AS kind, event AS action,
                       NULL AS direction, NULL AS transaction_id, NULL AS id_tag, details_json AS payload
                FROM runtime_events WHERE id > ?
            )
            ORDER BY kind, id LIMIT ?
        """, (cursor["ocpp"], cursor["runtime"], batch_size)).fetchall()
    finally:
        connection.close()
    # Advance per-table cursors even for events suppressed by filters.
    for row in rows:
        cursor[row["kind"]] = max(cursor[row["kind"]], row["id"])
    start = _time(since) if since else None
    stop = _time(until) if until else None
    result = [row for row in rows
            if (charger_id is None or row["charger_id"] == charger_id)
            and (transaction_id is None or row["transaction_id"] == transaction_id)
            and (start is None or row["occurred_at"] >= start)
            and (stop is None or row["occurred_at"] <= stop)]
    return sorted(result, key=lambda row: (row['occurred_at'], row['kind'], row['id']))


def follow_events(args, initial_rows, *, cursor, since=None, until=None, interval=0.5):
    """Print history first, then stream only newly persisted rows until Ctrl+C."""
    initial_rows = [row for row in initial_rows if row['id'] <= cursor[row['kind']]]
    if initial_rows:
        _emit(args, initial_rows)
    try:
        while True:
            rows = _new_events(args.data_dir, cursor, charger_id=args.charger,
                               transaction_id=args.transaction,
                               since=since, until=until)
            if rows:
                _emit(args, rows)
                continue  # drain backlog before sleeping
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def _emit(args, rows):
    if args.json:
        # JSONL: exactly one machine-readable record per line.
        for row in rows:
            item = event_record(row)
            if args.raw:
                item["payload"] = json.loads(row["payload"] or "{}")
            print(json.dumps(item, ensure_ascii=False), flush=True)
    else:
        print(format_events(rows, verbose=args.verbose), flush=True)
