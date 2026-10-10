"""Read-only operational reports from persisted CSMS evidence."""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ocpp_csms.evidence.alerts import classify_events
from ocpp_csms.evidence.diagnostics import events_between
from ocpp_csms.evidence.store import DATABASE_FILENAME
from ocpp_csms.status import appliance_status
from ocpp_csms.output import json_command_result

SCHEMA = "ocpp-csms/report/v1"


def _utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def build_report(data_dir: str | Path, *, since: datetime, until: datetime,
                 charger_id: str | None = None) -> dict[str, Any]:
    """Period activity uses received transaction times; health is a current snapshot."""
    if since > until:
        raise ValueError("--since must be earlier than or equal to --until")
    root = Path(data_dir).expanduser()
    start, end = _utc(since), _utc(until)
    database = root / DATABASE_FILENAME
    transactions: list[dict[str, Any]] = []
    if database.exists():
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("""
                SELECT transaction_id, charger_id, connector_id, id_tag, state,
                       started_at, stopped_at, start_received_at, stop_received_at,
                       meter_start, meter_stop
                FROM transactions
                WHERE COALESCE(start_received_at, stop_received_at) >= ?
                  AND COALESCE(start_received_at, stop_received_at) <= ?
                  AND (? IS NULL OR charger_id = ?)
                ORDER BY COALESCE(start_received_at, stop_received_at), transaction_id
            """, (start, end, charger_id, charger_id)).fetchall()
            for row in rows:
                r = dict(row)
                energy = None
                if r["meter_start"] is not None and r["meter_stop"] is not None:
                    delta = r["meter_stop"] - r["meter_start"]
                    if delta >= 0:
                        energy = round(delta / 1000, 3)
                duration = None
                if r["started_at"] and r["stopped_at"]:
                    try:
                        a = datetime.fromisoformat(r["started_at"].replace("Z", "+00:00"))
                        b = datetime.fromisoformat(r["stopped_at"].replace("Z", "+00:00"))
                        if b >= a:
                            duration = int((b-a).total_seconds())
                    except ValueError:
                        pass
                transactions.append({
                    "transaction_id": r["transaction_id"],
                    "charger_id": r["charger_id"], "connector_id": r["connector_id"],
                    "rfid": r["id_tag"], "state": r["state"],
                    "started_at": r["started_at"], "stopped_at": r["stopped_at"],
                    "energy_kwh": energy, "duration_seconds": duration,
                })
    measured = [t["energy_kwh"] for t in transactions if t["energy_kwh"] is not None]
    summary = {
        "transactions": len(transactions),
        "completed": sum(t["state"] == "stopped" for t in transactions),
        "incomplete": sum(t["state"] != "stopped" for t in transactions),
        "measured": len(measured),
        "energy_kwh": round(sum(measured), 3),
    }
    alerts = classify_events(events_between(root, charger_id=charger_id, since=start,
                                            until=end, limit=None))
    counts = dict(sorted(Counter(a["severity"] for a in alerts).items()))
    current = appliance_status(root)
    chargers = [
        {"charger_id": c.charger_id, "connected": c.connected,
         "last_seen": c.last_seen, "status": c.derived_status or c.status,
         "error_code": c.error_code}
        for c in current.get("chargers", [])
        if charger_id is None or c.charger_id == charger_id
    ]
    return json_command_result({
        "period": {"since": start, "until": end},
        "charger_filter": charger_id,
        "transactions": transactions,
        "subtotal": summary,
        "alerts": {"count": len(alerts), "by_severity": counts,
                   "events": alerts},
        "current_health": {"as_of": datetime.now(timezone.utc).isoformat(),
                           "chargers": chargers},
    }, schema=SCHEMA)


def format_report(report: dict[str, Any]) -> str:
    data = report["data"]
    lines = [f"OCPP-CSMS report: {data['period']['since']} to {data['period']['until']}",
             "", "Transactions:",
             "ID | CHARGER | C | RFID | STATE | ENERGY (kWh) | DURATION (s)"]
    for t in data["transactions"]:
        lines.append(" | ".join(str(v) if v is not None else "-" for v in (
            t["transaction_id"], t["charger_id"], t["connector_id"],
            t["rfid"], t["state"], t["energy_kwh"], t["duration_seconds"]
        )))
    sub = data["subtotal"]
    lines.append(f"SUBTOTAL | {sub['transactions']} transactions | "
                 f"{sub['completed']} completed | {sub['incomplete']} incomplete | "
                 f"{sub['energy_kwh']:.3f} kWh ({sub['measured']}/{sub['transactions']} measured)")
    lines.extend(["", f"Alerts: {data['alerts']['count']} "
                  f"({', '.join(f'{k}: {v}' for k, v in data['alerts']['by_severity'].items()) or 'none'})",
                  "", f"Current charger health (as of {data['current_health']['as_of']}):"])
    for charger in data["current_health"]["chargers"]:
        lines.append(f"{charger['charger_id']}: "
                     f"{'connected' if charger['connected'] else 'disconnected'}"
                     f" | {charger['status'] or 'Unknown'} | last seen {charger['last_seen'] or '-'}")
    return "\n".join(lines)
