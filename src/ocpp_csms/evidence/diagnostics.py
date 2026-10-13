from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ocpp_csms.evidence.store import DATABASE_FILENAME


def _time(value: str) -> str:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def events_between(
    data_dir: str | Path,
    *,
    charger_id: str | None = None,
    transaction_id: int | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = 200,
) -> list[sqlite3.Row]:
    since = _time(since) if since else None
    until = _time(until) if until else None
    if since and until and since > until:
        raise ValueError("--since must be earlier than or equal to --until")

    database = Path(data_dir).expanduser() / DATABASE_FILENAME
    if not database.exists():
        return []

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT * FROM (
                SELECT id, received_at AS occurred_at, charger_id, 'ocpp' AS kind,
                       action, direction, transaction_id, id_tag, payload_json AS payload
                FROM events
                UNION ALL
                SELECT id, occurred_at, charger_id, 'runtime', event, NULL, NULL, NULL, details_json
                FROM runtime_events
            )
            WHERE (? IS NULL OR charger_id = ?)
              AND (? IS NULL OR transaction_id = ?)
              AND (? IS NULL OR occurred_at >= ?)
              AND (? IS NULL OR occurred_at <= ?)
            ORDER BY occurred_at DESC, id DESC
            LIMIT ?
            """,
            (
                charger_id,
                charger_id,
                transaction_id,
                transaction_id,
                since,
                since,
                until,
                until,
                limit if limit is not None else -1,
            ),
        ).fetchall()
    finally:
        connection.close()
    rows.reverse()
    return rows


def transaction_events(
    data_dir: str | Path,
    transaction_id: int,
    *,
    limit: int = 1000,
) -> list[sqlite3.Row]:
    """Return OCPP evidence explicitly associated with one transaction."""
    database = Path(data_dir).expanduser() / DATABASE_FILENAME
    if not database.exists():
        return []

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            """
            SELECT id, received_at AS occurred_at, charger_id, 'ocpp' AS kind,
                   action, direction, transaction_id, id_tag, payload_json AS payload
            FROM events
            WHERE transaction_id = ?
            ORDER BY occurred_at ASC, id ASC
            LIMIT ?
            """,
            (int(transaction_id), limit),
        ).fetchall()
    finally:
        connection.close()


def _summary(row: sqlite3.Row, *, detail: bool = False) -> str:
    """Summarize one stored event without assuming frame correlation."""
    if row["kind"] == "runtime":
        return str(row["action"]).replace("_", " ")

    try:
        payload = json.loads(row["payload"] or "{}")
    except (TypeError, ValueError):
        return f"{row['action']} [invalid payload]"
    if not isinstance(payload, dict):
        return f"{row['action']} [unexpected payload]"

    action = str(row["action"])
    direction = row["direction"]
    details = []
    connector = payload.get("connector_id")
    if connector is not None:
        details.append(f"C{connector}")
    if row["id_tag"]:
        details.append(f"RFID {row['id_tag']}")
    if row["transaction_id"] is not None:
        details.append(f"tx={row['transaction_id']}")

    if action == "StatusNotification":
        details.extend(str(value) for value in (
            payload.get("status"), payload.get("error_code"), payload.get("info")
        ) if value is not None and value != "" and value != "NoError")
    elif direction == "out":
        status = payload.get("status")
        info = payload.get("idTagInfo")
        if status is None and isinstance(info, dict):
            status = info.get("status")
        if status is not None:
            details.append(str(status))
        if payload.get("transaction_id") is not None and row["transaction_id"] is None:
            details.append(f"tx={payload['transaction_id']}")
        if action == "Heartbeat" and (payload.get("currentTime") is not None or payload.get("current_time") is not None):
            details.append("response")
        if not details:
            details.append("response")
        # Keep unexpected response details visible rather than presenting an
        # unfamiliar payload as a routine acknowledgement.
        known = {"status", "idTagInfo", "transaction_id", "current_time", "currentTime"}
        extras = {k: v for k, v in payload.items() if k not in known}
        if extras:
            details.append(f"extra={json.dumps(extras, sort_keys=True, ensure_ascii=False) if detail else len(extras)}")
        return "→ " + " ".join([action, *details])
    elif action in {"StartTransaction", "StopTransaction"}:
        for key in ("reason", "meter_start", "meter_stop"):
            if payload.get(key) is not None:
                details.append(f"{key}={payload[key]}")

    return " ".join([action, *details])


def _group_key(row: sqlite3.Row) -> tuple[object, ...] | None:
    """Only collapse adjacent, independently identifiable routine records.

    In particular, stored events have no OCPP message ID: requests and
    responses are never paired, even when adjacent.
    """
    if row["kind"] != "ocpp" or row["transaction_id"] is not None or row["id_tag"] is not None:
        return None
    try:
        payload = json.loads(row["payload"] or "{}")
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    action, direction = row["action"], row["direction"]
    if action == "Heartbeat":
        if direction == "in" and not payload:
            return ("heartbeat-request", row["charger_id"])
        if (direction == "out" and len(payload) == 1
                and any(isinstance(payload.get(k), str) and payload[k] for k in ("currentTime", "current_time"))):
            return ("heartbeat-response", row["charger_id"])
        return None
    if action == "StatusNotification" and direction == "in":
        status = payload.get("status")
        error = payload.get("error_code")
        info = payload.get("info")
        if status not in {"Available", "Preparing", "Charging", "SuspendedEV",
                          "SuspendedEVSE", "Finishing", "Reserved", "Unavailable"}:
            return None
        if error not in (None, "NoError") or info not in (None, ""):
            return None
        connector = payload.get("connector_id")
        if not isinstance(connector, int) or isinstance(connector, bool):
            return None
        # Unexpected fields may carry important evidence; do not collapse them.
        if set(payload) - {"connector_id", "status", "error_code", "info",
                            "timestamp", "vendor_id", "vendor_error_code"}:
            return None
        if payload.get("vendor_id") or payload.get("vendor_error_code"):
            return None
        return ("status", row["charger_id"], connector, status, error, info)
    return None


def _meter_key(row: sqlite3.Row) -> tuple[object, ...] | None:
    """Identify routine MeterValues frames; adjacency is not OCPP correlation."""
    if row["kind"] != "ocpp" or row["action"] != "MeterValues" or row["id_tag"] is not None:
        return None
    try:
        payload = json.loads(row["payload"] or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if row["direction"] == "out":
        # MeterValues.conf is empty in OCPP 1.6. A response has no
        # connector/transaction identifier of its own.
        if not payload and row["transaction_id"] is None:
            return ("meter-response", row["charger_id"])
        return None
    if row["direction"] != "in":
        return None
    if set(payload) - {"connector_id", "transaction_id", "meter_value"}:
        return None
    connector = payload.get("connector_id")
    samples = payload.get("meter_value")
    if (not isinstance(connector, int) or isinstance(connector, bool)
            or connector < 0 or not isinstance(samples, list) or not samples
            or not all(isinstance(sample, dict) and sample for sample in samples)):
        return None
    tx_payload = payload.get("transaction_id")
    tx_row = row["transaction_id"]
    if tx_payload is not None and (not isinstance(tx_payload, int) or isinstance(tx_payload, bool)):
        return None
    if tx_row is not None and (not isinstance(tx_row, int) or isinstance(tx_row, bool)):
        return None
    if tx_payload is not None and tx_row is not None and tx_payload != tx_row:
        return None
    return ("meter-request", row["charger_id"], connector, tx_payload if tx_payload is not None else tx_row)


def _consume_exchanges(
    rows: list[sqlite3.Row], start: int, request_key: tuple[object, ...],
    response_key: tuple[object, ...], key_fn: Any,
) -> tuple[int, int]:
    """Count only observed adjacent requests followed by ordinary replies."""
    index = start
    count = 0
    while index + 1 < len(rows):
        if key_fn(rows[index]) != request_key or key_fn(rows[index + 1]) != response_key:
            break
        index += 2
        count += 1
    return index, count


def format_events(rows: list[sqlite3.Row], heading: str | None = None, *, verbose: bool = False, detail: bool = False) -> str:
    if not rows:
        return "No matching events."
    lines = [heading, ""] if heading else []
    if verbose:
        for row in rows:
            charger = f" {row['charger_id']}" if row["charger_id"] else ""
            direction = f" {row['direction']}" if row["direction"] else ""
            tx = f" tx={row['transaction_id']}" if row["transaction_id"] is not None else ""
            rfid = f" RFID={row['id_tag']}" if row["id_tag"] else ""
            lines.append(
                f"{row['occurred_at']}{charger} [{row['kind']}{direction}] "
                f"{row['action']}{tx}{rfid} payload={row['payload']}"
            )
        return "\n".join(lines)

    def append_group(group: list[sqlite3.Row]) -> None:
        first, last = group[0], group[-1]
        charger = f" {first['charger_id']}" if first["charger_id"] else ""
        summary = _summary(first, detail=detail)
        if _group_key(first) is not None and first["action"] == "Heartbeat":
            summary = ("Heartbeat requests (responses not established)"
                       if first["direction"] == "in" else "→ Heartbeat responses")
        if len(group) == 1:
            lines.append(f"{first['occurred_at']}{charger}  {summary}")
        else:
            # These are counts of observed stored events, not inferred
            # request/response pairs or claims about unseen window edges.
            lines.append(
                f"{first['occurred_at']}–{last['occurred_at']}{charger}  "
                f"{summary} ×{len(group)}"
            )

    # Adjacency indicates an observed exchange, not protocol-level correlation.
    index = 0
    while index < len(rows):
        first = rows[index]
        meter = _meter_key(first)
        if meter is not None and meter[0] == "meter-request":
            count = 0
            end_at = first["occurred_at"]
            while index + 1 < len(rows):
                request, response = rows[index], rows[index + 1]
                if (_meter_key(request) != meter
                        or _meter_key(response) != ("meter-response", first["charger_id"])):
                    break
                count += 1
                end_at = response["occurred_at"]
                index += 2
            if count:
                charger = f" {first['charger_id']}" if first["charger_id"] else ""
                span = (f"{first['occurred_at']}–{end_at}" if count > 1
                        else first["occurred_at"])
                tx = f" tx={meter[3]}" if meter[3] is not None else ""
                lines.append(f"{span}{charger}  MeterValues C{meter[2]}{tx} ↔ ×{count}")
                continue
        if meter is not None and meter[0] == "meter-request":
            end = index
            while end < len(rows) and _meter_key(rows[end]) == meter:
                end += 1
            if end > index + 1 and (end == len(rows) or
                    _meter_key(rows[end]) != ("meter-response", first["charger_id"])):
                charger = f" {first['charger_id']}" if first["charger_id"] else ""
                tx = f" tx={meter[3]}" if meter[3] is not None else ""
                lines.append(
                    f"{first['occurred_at']}–{rows[end - 1]['occurred_at']}{charger}  "
                    f"MeterValues C{meter[2]}{tx} requests (responses not established) ×{end - index}"
                )
                index = end
                continue
        if _group_key(first) == ("heartbeat-request", first["charger_id"]):
            # Preserve unmatched leading requests, while allowing the final
            # request to form an adjacent observed exchange with a response.
            request_key = ("heartbeat-request", first["charger_id"])
            response_key = ("heartbeat-response", first["charger_id"])
            end = index
            while end < len(rows) and _group_key(rows[end]) == request_key:
                end += 1
            if end - index > 1 and end < len(rows) and _group_key(rows[end]) == response_key:
                append_group(rows[index:end - 1])
                index = end - 1
                first = rows[index]
            count = 0
            end_at = first["occurred_at"]
            while index + 1 < len(rows):
                request, response = rows[index], rows[index + 1]
                if (_group_key(request) != ("heartbeat-request", first["charger_id"])
                        or _group_key(response) != ("heartbeat-response", first["charger_id"])):
                    break
                count += 1
                end_at = response["occurred_at"]
                index += 2
            if count:
                charger = f" {first['charger_id']}" if first["charger_id"] else ""
                span = (f"{first['occurred_at']}–{end_at}" if count > 1
                        else first["occurred_at"])
                lines.append(f"{span}{charger}  Heartbeat ↔ ×{count}")
                continue
        key = _group_key(first)
        group = [first]
        index += 1
        if key is not None:
            while index < len(rows) and _group_key(rows[index]) == key:
                group.append(rows[index])
                index += 1
        append_group(group)
    return "\n".join(lines)


def explain(
    data_dir: str | Path,
    charger_id: str,
    *,
    at: str | None = None,
    since: str | None = None,
    until: str | None = None,
    minutes: int = 10,
) -> str:
    if at:
        if since or until:
            raise ValueError("use either --at or --since/--until, not both")
        center = datetime.fromisoformat(_time(at).replace("Z", "+00:00"))
        since = (center - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
        until = (center + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
        heading = f"{charger_id} around {at}"
    else:
        if not since or not until:
            raise ValueError("explain requires --at TIME or --since TIME --until TIME")
        heading = f"{charger_id} from {since} until {until}"

    rows = events_between(data_dir, charger_id=charger_id, since=since, until=until, limit=1000)
    return format_events(rows, heading)
