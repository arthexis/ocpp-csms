from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from ocpp_csms.transaction_query import TransactionView


def _text(value: object | None, default: str = "-") -> str:
    return default if value is None or value == "" else str(value)


def _timestamp(value: object | None) -> str:
    if not isinstance(value, str):
        return "-"
    return value


def _age_key(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _archive_path(view: TransactionView) -> str:
    parts = view.path.parts
    try:
        index = parts.index("transactions")
    except ValueError:
        return str(view.path)
    return str(Path(*parts[index:]))


def format_transactions(views: Iterable[TransactionView]) -> str:
    rows = list(views)
    if not rows:
        return "No transactions."

    headers = ("TXN", "STATUS", "CHARGER", "CP", "RFID", "UPDATED")
    body = [
        (
            str(view.transaction_id),
            view.status or "unknown",
            view.charge_point_id or "-",
            _text(view.connector_id, "?"),
            _text(view.id_tag),
            _age_key(view.updated_at),
        )
        for view in rows
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in body))
        for index in range(len(headers))
    ]

    def line(values: tuple[str, ...]) -> str:
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(values)).rstrip()

    return "\n".join([line(headers), *(line(row) for row in body)])


def format_transaction(view: TransactionView) -> str:
    record = view.record
    start = record.get("start") if isinstance(record.get("start"), dict) else {}
    stop = record.get("stop") if isinstance(record.get("stop"), dict) else {}
    origin = record.get("origin") or ("local" if start else "recovered")

    lines = [
        f"Transaction {view.transaction_id}",
        f"Status:       {view.status or 'unknown'}",
        f"Origin:       {origin}",
        f"Charger:      {view.charge_point_id or '-'}",
        f"Connector:    {_text(view.connector_id, '?')}",
        f"RFID:         {_text(view.id_tag)}",
        "",
        f"Started:      {_timestamp(start.get('timestamp'))}",
        f"Stopped:      {_timestamp(stop.get('timestamp'))}",
        f"Last update:  {_age_key(view.updated_at)}",
        f"MeterValues:  {len(record.get('meter_values') or [])}",
        f"Archive:      {_archive_path(view)}",
    ]
    if view.unresolved:
        lines.extend(("", f"Warnings:     {len(view.unresolved)} unresolved transaction evidence record(s)"))
    return "\n".join(lines)
