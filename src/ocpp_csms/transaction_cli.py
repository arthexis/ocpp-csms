from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

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


def _parse_time(value: object | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _duration(started: object | None, stopped: object | None) -> str | None:
    start = _parse_time(started)
    stop = _parse_time(stopped)
    if start is None or stop is None or stop < start:
        return None
    seconds = int((stop - start).total_seconds())
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _energy_wh(start: dict[str, Any], stop: dict[str, Any]) -> int | None:
    try:
        meter_start = int(start["meter_start"])
        meter_stop = int(stop["meter_stop"])
    except (KeyError, TypeError, ValueError):
        return None
    consumed = meter_stop - meter_start
    return consumed if consumed >= 0 else None


def _format_energy(energy_wh: int) -> str:
    if energy_wh >= 1000:
        return f"{energy_wh / 1000:.3f} kWh"
    return f"{energy_wh} Wh"


def _transaction_energy(view: TransactionView) -> str:
    record = view.record
    start = record.get("start") if isinstance(record.get("start"), dict) else {}
    stop = record.get("stop") if isinstance(record.get("stop"), dict) else {}
    energy_wh = _energy_wh(start, stop)
    return _format_energy(energy_wh) if energy_wh is not None else "-"


def _meter_summary(record: dict[str, Any]) -> dict[str, object]:
    payloads = record.get("meter_values")
    if not isinstance(payloads, list):
        payloads = []

    sample_count = 0
    timestamps: list[str] = []
    latest_energy: tuple[str, str] | None = None
    latest_power: tuple[str, str] | None = None

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        meter_values = payload.get("meter_value")
        if not isinstance(meter_values, list):
            continue
        for entry in meter_values:
            if not isinstance(entry, dict):
                continue
            timestamp = entry.get("timestamp")
            if isinstance(timestamp, str):
                timestamps.append(timestamp)
            samples = entry.get("sampled_value")
            if not isinstance(samples, list):
                continue
            for sample in samples:
                if not isinstance(sample, dict):
                    continue
                sample_count += 1
                value = sample.get("value")
                measurand = sample.get("measurand")
                unit = sample.get("unit")
                if not isinstance(value, str) or not isinstance(measurand, str) or not isinstance(unit, str):
                    continue
                if measurand == "Energy.Active.Import.Register":
                    latest_energy = (value, unit)
                elif measurand == "Power.Active.Import":
                    latest_power = (value, unit)

    return {
        "messages": len(payloads),
        "samples": sample_count,
        "first": min(timestamps) if timestamps else None,
        "last": max(timestamps) if timestamps else None,
        "latest_energy": latest_energy,
        "latest_power": latest_power,
    }


def format_transactions(views: Iterable[TransactionView]) -> str:
    rows = list(views)
    if not rows:
        return "No transactions."

    headers = ("TXN", "STATUS", "CHARGER", "CP", "RFID", "ENERGY", "UPDATED")
    body = [
        (
            str(view.transaction_id),
            view.status or "unknown",
            view.charge_point_id or "-",
            _text(view.connector_id, "?"),
            _text(view.id_tag),
            _transaction_energy(view),
            _age_key(view.activity_at),
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
    duration = _duration(start.get("timestamp"), stop.get("timestamp"))
    energy_wh = _energy_wh(start, stop)
    meter = _meter_summary(record)

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
    ]
    if duration is not None:
        lines.append(f"Duration:     {duration}")
    lines.extend(
        (
            f"Last update:  {_age_key(view.activity_at)}",
            f"Meter start:  {_text(start.get('meter_start'))}",
            f"Meter stop:   {_text(stop.get('meter_stop'))}",
        )
    )
    if energy_wh is not None:
        lines.append(f"Energy:       {_format_energy(energy_wh)}")

    lines.extend(
        (
            "",
            "MeterValues:",
            f"  messages:   {meter['messages']}",
            f"  samples:    {meter['samples']}",
            f"  first:      {_text(meter['first'])}",
            f"  last:       {_text(meter['last'])}",
        )
    )
    if meter["latest_energy"] is not None:
        value, unit = meter["latest_energy"]
        lines.append(f"  latest energy: {value} {unit}")
    if meter["latest_power"] is not None:
        value, unit = meter["latest_power"]
        lines.append(f"  latest power: {value} {unit}")

    if origin == "recovered" and not start:
        recovered_by = "StopTransaction" if stop else "MeterValues" if record.get("meter_values") else "historical traffic"
        lines.extend(
            (
                "",
                "Recovery:",
                "  start:      unknown",
                f"  recovered:  {recovered_by}",
            )
        )

    if view.unresolved:
        lines.extend(("", "Warnings:"))
        for unresolved in view.unresolved:
            reason = unresolved.get("reason") or "unknown"
            message_type = unresolved.get("message_type") or "OCPP"
            lines.append(f"  unresolved {message_type}: {reason}")

    lines.extend(("", f"Archive:      {_archive_path(view)}"))
    return "\n".join(lines)
