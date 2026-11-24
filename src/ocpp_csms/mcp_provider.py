"""Read-only, independently invoked GWay MCP provider (stage 1)."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from ocpp_csms.evidence.store import DATABASE_FILENAME
from ocpp_csms.schema import CURRENT_SCHEMA_VERSION
from ocpp_csms.status import appliance_status

PROTOCOL = "2025-03-26"
MAX_MESSAGE = 131072
MAX_ITEMS = 100
MAX_STATUS_ROWS = 1000
DEFAULT_PAGE = 20


class ToolError(ValueError):
    pass


def _tool(name: str, description: str, targeted: bool, *, paged: bool = False) -> dict[str, Any]:
    properties = {"charge_point_id": {"type": "string", "minLength": 1, "maxLength": 256}} if targeted else {}
    required = list(properties)
    if paged:
        properties.update({"limit": {"type": "integer", "minimum": 1, "maximum": MAX_ITEMS, "default": DEFAULT_PAGE},
                           "before_id": {"type": "integer", "minimum": 1, "maximum": 9223372036854775807}})
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object", "properties": properties,
            "required": required, "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    }


def tools() -> list[dict[str, Any]]:
    return [
        _tool("charger_list", "List recorded charge points on this CSMS; no OCPP traffic.", False),
        _tool("charger_status", "Read recorded charger and connector observations; no OCPP traffic.", True),
        _tool("connector_list", "Read recorded connector observations for a charge point; no OCPP traffic.", True),
        _tool("transaction_list", "List recorded transaction summaries by descending transaction ID, without RFID or payloads.", True, paged=True),
        _tool("event_list", "List recorded OCPP event metadata by descending evidence ID, without payloads or RFID.", True, paged=True),
    ]


@contextmanager
def _evidence(data_dir: Path):
    database = data_dir / DATABASE_FILENAME
    if not database.is_file():
        raise ToolError("evidence_unavailable")
    deadline = time.monotonic() + 3
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            # Python 3.10 lacks setlimit; gateway still bounds subprocess time.
            if hasattr(db, "setlimit"):
                db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 65536)
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            if db.execute("PRAGMA user_version").fetchone()[0] != CURRENT_SCHEMA_VERSION:
                raise ToolError("schema_mismatch")
            db.execute("BEGIN")
            yield db
    except (OSError, sqlite3.Error):
        raise ToolError("evidence_unavailable") from None


def _known_ids(db, cp=None):
    tables = ("events", "runtime_events", "connector_status", "transactions")
    if cp is not None:
        return (cp,) if any(db.execute(
            f"SELECT 1 FROM {table} WHERE charger_id = ? LIMIT 1", (cp,)
        ).fetchone() for table in tables) else ()
    query = " UNION ".join(f"SELECT charger_id FROM {table} WHERE charger_id IS NOT NULL AND charger_id != ''" for table in tables)
    rows = db.execute(query + " ORDER BY charger_id LIMIT ?", (MAX_ITEMS + 1,)).fetchall()
    if len(rows) > MAX_ITEMS:
        raise ToolError("result_limit_exceeded")
    return tuple(row[0] for row in rows)


def read_status(data_dir: Path, cp: str | None = None) -> dict[str, Any]:
    with _evidence(data_dir) as db:
        ids = _known_ids(db, cp)
        budget = MAX_STATUS_ROWS
        for charger_id in ids:
            for table, condition in (("connector_status", ""), ("transactions", " AND state = 'open'")):
                count = db.execute(
                    f"SELECT count(*) FROM (SELECT 1 FROM {table} WHERE charger_id = ?{condition} LIMIT ?)",
                    (charger_id, budget + 1),
                ).fetchone()[0]
                budget -= count
                if budget < 0:
                    raise ToolError("result_limit_exceeded")
        result = appliance_status(data_dir, connection=db, charger_ids=ids, include_rfid=False)
        if result.get("database") != "ok":
            raise ToolError("evidence_unavailable")
        return result


def read_page(data_dir: Path, name: str, cp: str, limit: int, before_id: int | None):
    # Explicit columns only: no payload_json, details_json, id_tag or arbitrary config.
    if name == "transaction_list":
        table, key = "transactions", "transaction_id"
        columns = ("transaction_id", "connector_id", "state", "started_at", "stopped_at",
                   "start_received_at", "stop_received_at", "last_activity_at")
    else:
        table, key = "events", "id"
        columns = ("id", "received_at", "action", "direction", "transaction_id", "charger_timestamp")
    where = "charger_id = ?"
    params = [cp]
    if before_id is not None:
        where += f" AND {key} < ?"
        params.append(before_id)
    params.append(limit + 1)
    with _evidence(data_dir) as db:
        if not _known_ids(db, cp):
            raise ToolError("charge_point_not_found")
        rows = db.execute(
            f"SELECT {', '.join(columns)} FROM {table} WHERE {where} ORDER BY {key} DESC LIMIT ?",
            params,
        ).fetchall()
    page = [dict(row) for row in rows[:limit]]
    return {"source": "stored_evidence", "charge_point_id": cp,
            "items": page, "next_before_id": page[-1][key] if len(rows) > limit else None}


def _connector(item: Any) -> dict[str, Any]:
    return {
        "connector_id": item.connector_id, "status": item.status,
        "derived_status": item.derived_status, "error_code": item.error_code,
        "transaction_id": item.transaction_id, "started_at": item.started_at,
    }


def _charger(item: Any, live: bool) -> dict[str, Any]:
    return {
        "charge_point_id": item.charger_id,
        "connected": item.connected if live else None,
        "last_seen": item.last_seen, "status": item.status,
        "derived_status": item.derived_status, "error_code": item.error_code,
        "availability": item.availability if live else "unknown",
        "activity": item.activity if live else "unknown",
    }


def invoke(data_dir: Path, name: str, arguments: Any) -> dict[str, Any]:
    if type(arguments) is not dict:
        raise ToolError("invalid_arguments")
    cp = None
    if name == "charger_list":
        if arguments:
            raise ToolError("invalid_arguments")
    elif name in {"charger_status", "connector_list", "transaction_list", "event_list"}:
        cp = arguments.get("charge_point_id")
        paged = name in {"transaction_list", "event_list"}
        allowed = {"charge_point_id", "limit", "before_id"} if paged else {"charge_point_id"}
        if set(arguments) - allowed or type(cp) is not str or not 1 <= len(cp) <= 256 or not cp.strip():
            raise ToolError("invalid_arguments")
        if paged:
            limit = arguments.get("limit", DEFAULT_PAGE)
            before_id = arguments.get("before_id")
            if (type(limit) is not int or not 1 <= limit <= MAX_ITEMS
                    or ("before_id" in arguments and (type(before_id) is not int or not 1 <= before_id <= 9223372036854775807))):
                raise ToolError("invalid_arguments")
            return read_page(data_dir, name, cp, limit, before_id)
    else:
        raise ToolError("unknown_tool")
    snapshot = read_status(data_dir, cp)
    live = snapshot.get("server") == "running" and bool(snapshot.get("started_at"))
    items = snapshot["chargers"]
    result: dict[str, Any] = {"source": "stored_evidence", "server": snapshot.get("server")}
    if name == "charger_list":
        if len(items) > MAX_ITEMS:
            raise ToolError("result_limit_exceeded")
        result["chargers"] = [_charger(item, live) for item in items]
        return result
    matches = [item for item in items if item.charger_id == cp]
    if len(matches) != 1:
        raise ToolError("charge_point_not_found")
    item = matches[0]
    if len(item.connectors) > MAX_ITEMS:
        raise ToolError("result_limit_exceeded")
    result["connectors"] = [_connector(connector) for connector in item.connectors]
    if name == "charger_status":
        result["charger"] = _charger(item, live)
    else:
        result["charge_point_id"] = cp
    return result


def _content(value: dict[str, Any], *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, allow_nan=False)}], "isError": error}


def dispatch(data_dir: Path, request: Any) -> dict[str, Any] | None:
    if type(request) is not dict or request.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    if "id" not in request:
        return None
    if type(request["id"]) not in (int, str) or (isinstance(request["id"], str) and len(request["id"]) > 128):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request ID"}}
    response: dict[str, Any] = {"jsonrpc": "2.0", "id": request["id"]}
    method = request.get("method")
    params = request.get("params", {})
    if method == "initialize" and isinstance(params, dict):
        response["result"] = {
            "protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
            "serverInfo": {"name": "ocpp-csms-mcp", "version": "0.1.0"},
        }
    elif method == "tools/list":
        response["result"] = {"tools": tools()}
    elif method == "tools/call":
        try:
            if type(params) is not dict or type(params.get("name")) is not str:
                raise ToolError("invalid_arguments")
            response["result"] = _content(invoke(data_dir, params["name"], params.get("arguments", {})))
        except ToolError as exc:
            response["result"] = _content({"error": str(exc)}, error=True)
    else:
        response["error"] = {"code": -32601, "message": "Method not found"}
    return response


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args()
    if not args.data_dir.is_absolute():
        parser.error("--data-dir must be absolute")
    while True:
        line = sys.stdin.buffer.readline(MAX_MESSAGE + 1)
        if not line:
            return 0
        if len(line) > MAX_MESSAGE:
            return 1
        try:
            request = json.loads(line)
            response = dispatch(args.data_dir, request)
            if response is None:
                continue
            encoded = json.dumps(response, allow_nan=False).encode() + b"\n"
            if len(encoded) > MAX_MESSAGE:
                encoded = json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": _content({"error": "result_limit_exceeded"}, error=True)}).encode() + b"\n"
        except (ValueError, TypeError, RecursionError):
            return 1
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    raise SystemExit(main())
