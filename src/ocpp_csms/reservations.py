"""Reconstruct reservation requests from persisted OCPP evidence, without charger queries."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from ocpp_csms.evidence.store import DATABASE_FILENAME

def _date(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except ValueError:
        return None

def list_reservations(data_dir, *, charger=None, include_all=False, since=None, now=None):
    database = Path(data_dir).expanduser() / DATABASE_FILENAME
    if not database.exists():
        return []
    with sqlite3.connect(database) as con:
        rows = con.execute("""SELECT id, received_at, charger_id, action, direction, payload_json
                              FROM events
                              WHERE action IN ('ReserveNow', 'CancelReservation', 'StartTransaction')
                              ORDER BY id ASC""").fetchall()
    # Requests/responses have no durable OCPP message correlation ID in this
    # evidence schema. Attribute a response only if exactly one candidate is
    # outstanding for that CP and action; otherwise leave outcomes unknown.
    records = {}
    outstanding = {}
    for event_id, when, cp, action, direction, raw in rows:
        try:
            payload = json.loads(raw or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        if action == "StartTransaction" and direction == "in":
            reservation_id = payload.get("reservation_id")
            if isinstance(reservation_id, int):
                row = records.get((cp, reservation_id))
                if row is not None and row["status"] in {"Accepted", "Requested"}:
                    row["status"] = "Used"
            continue
        pending_key = (cp, action)
        if direction == "out":
            reservation_id = payload.get("reservation_id")
            if not isinstance(reservation_id, int):
                continue
            if action == "ReserveNow":
                key = (cp, reservation_id)
                records[key] = {"id": reservation_id, "cp": cp, "connector": payload.get("connector_id"),
                                "rfid": payload.get("id_tag"), "requested": when,
                                "expires": payload.get("expiry_date"), "status": "Requested",
                                "response": None, "expired": False}
            outstanding.setdefault(pending_key, []).append((reservation_id, event_id))
        elif direction == "in" and action in {"ReserveNow", "CancelReservation"}:
            candidates = outstanding.get(pending_key, [])
            if len(candidates) == 1:
                reservation_id, _ = candidates.pop()
                row = records.get((cp, reservation_id))
                status = payload.get("status")
                if row is not None and isinstance(status, str):
                    if action == "ReserveNow":
                        row["response"] = status
                        row["status"] = "Accepted" if status == "Accepted" else "Rejected" if status == "Rejected" else status
                    elif status == "Accepted":
                        row["status"] = "Canceled"
            # Ambiguous responses are deliberately not assigned to any request.
    current = now or datetime.now(timezone.utc)
    lower_bound = _date(since) if since else None
    result = []
    for row in records.values():
        if charger and row["cp"] != charger:
            continue
        if lower_bound and (t := _date(row["requested"])) and t < lower_bound:
            continue
        expiry = _date(row["expires"])
        row["expired"] = bool(expiry and expiry <= current)
        if row["expired"] and row["status"] in {"Accepted", "Requested"}:
            row["status"] = "Expired"
        if include_all or row["status"] in {"Accepted", "Requested"}:
            result.append(row)
    return sorted(result, key=lambda r: (r["requested"], r["cp"], r["id"]), reverse=True)
