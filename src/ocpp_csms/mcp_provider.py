"""Read-only, independently invoked GWay MCP provider (stage 1)."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from ocpp_csms.evidence.store import DATABASE_FILENAME
from ocpp_csms.schema import CURRENT_SCHEMA_VERSION
from ocpp_csms.status import appliance_status

PROTOCOL = "2025-03-26"
MAX_MESSAGE = 131072
MAX_ITEMS = 100


class ToolError(ValueError):
    pass


def _tool(name: str, description: str, targeted: bool) -> dict[str, Any]:
    properties = {"charge_point_id": {"type": "string", "minLength": 1, "maxLength": 256}} if targeted else {}
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    }


def tools() -> list[dict[str, Any]]:
    return [
        _tool("charger_list", "List recorded charge points on this CSMS; no OCPP traffic.", False),
        _tool("charger_status", "Read recorded charger and connector observations; no OCPP traffic.", True),
        _tool("connector_list", "Read recorded connector observations for a charge point; no OCPP traffic.", True),
    ]


def read_status(data_dir: Path) -> dict[str, Any]:
    database = data_dir / DATABASE_FILENAME
    if not database.is_file():
        raise ToolError("evidence_unavailable")
    deadline = time.monotonic() + 3
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        if db.execute("PRAGMA user_version").fetchone()[0] != CURRENT_SCHEMA_VERSION:
            raise ToolError("schema_mismatch")
        db.execute("BEGIN")
        result = appliance_status(data_dir, connection=db)
        if result.get("database") != "ok":
            raise ToolError("evidence_unavailable")
        return result


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
    if name == "charger_list":
        if arguments:
            raise ToolError("invalid_arguments")
    elif name in {"charger_status", "connector_list"}:
        cp = arguments.get("charge_point_id")
        if set(arguments) != {"charge_point_id"} or type(cp) is not str or not 1 <= len(cp) <= 256 or not cp.strip():
            raise ToolError("invalid_arguments")
    else:
        raise ToolError("unknown_tool")
    try:
        snapshot = read_status(data_dir)
    except (OSError, sqlite3.Error):
        raise ToolError("evidence_unavailable") from None
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
