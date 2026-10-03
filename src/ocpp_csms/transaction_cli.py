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


def _age_key(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _archive_path(view: TransactionView) -> str:
    parts = view.path.parts
    try:
        index = parts.index("transactions")
    except ValueError:
        return str(view.path)
    return str(Path(*parts[index:]))


def _duration(start: object | None, stop: object | None) -> str | None:
    started = _parse_time(start)
    stopped = _parse_time(stop)
    if started is None or stopped is None or stopped < started:
        return None
    seconds = int((stopped - started).total_seconds())
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _energy_wh(start: dict[str, Any], stop: dict[str, Any]) -> int | None:
    try:
        meter_start = int(start["meter_start"])
        meter_stop = int(stop["meter_stop"])
    except (KeyError, TypeError, ValueError):
        return None
    if meter_stop < meter_start:
        return None
    return meter_stop - meter_start


def _format_energy(wh: int) -> str:
    if wh >= 1000:
        return f"{wh / 1000:.3f} kWh"
    return f"{wh} Wh"


def _meter_summary(record: dict[str, Any]) -> dict[str, Any]:
    messages = record.get("meter_values")
    if not isinstance(messages, list):
        messages = []

    timestamps: list[datetime] = []
    samples = 0
    latest: dict[tuple[str, str], tuple[datetime | None, str]] = {}

    for message in messages:
        if not isinstance(message, dict):
            continue
        meter_values = message.get("meter_value")
        if not isinstance(meter_values, list):
            continue
        for meter_value in meter_values:
            if not isinstance(meter_value, dict):
                continue
            sample_time = _parse_time(meter_value.get("timestamp"))
            if sample_time is not None:
                timestamps.append(sample_time)
            sampled_values = meter_value.get("sampled_value")
            if not isinstance(sampled_values, list):
                continue
            for sample in sampled_values:
                if not isinstance(sample, dict):
                    continue
                samples += 1
                # Only label a value when the charger explicitly supplied both
                # measurand and unit. OCPP defaults exist, but the operator view
                # should not imply semantics the charger did not put on the wire.
                measurand = sample.get("measurand")
                unit = sample.get("unit")
                value = sample.get("value")
                if not all(isinstance(item, str) and item for item in (measurand, unit, value)):
                    continue
                key = (measurand, unit)
                prior = latest.get(key)
                if prior is None or sample_time is None or prior[0] is None or sample_time >= prior[0]:
                    latest[key] = (sample_time, value)

    useful = {
        key: value
        for key, value in latest.items()
        if key[0] in {"Energy.Active.Import.Register", "Power.Active.Import"}
    }
    return {
        "messages": len(messages),
        "samples": samples,
        "first": min(timestamps) if timestamps else None,
        "last": max(timestamps) if timestamps else None,
        "latest": useful,
    }


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
            f"Last update:  {_age_key(view.updated_at)}",
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
            f"  first:      {_age_key(meter['first']) if meter['first'] else '-'}",
            f"  last:       {_age_key(meter['last']) if meter['last'] else '-'}",
        )
    )
    for (measurand, unit), (_, value) in sorted(meter["latest"].items()):
        label = "energy" if measurand == "Energy.Active.Import.Register" else "power"
        lines.append(f"  latest {label}: {value} {unit}")

    lines.extend(("", f"Archive:      {_archive_path(view)}"))

    if origin == "recovered":
        recovered_by = "StopTransaction" if stop else "MeterValues" if meter["messages"] else "historical evidence"
        lines.extend(("", "Recovery:", f"  start:      unknown", f"  recovered:  {recovered_by}"))

    if view.unresolved:
        lines.extend(("", "Warnings:"))
        for evidence in view.unresolved:
            reason = _text(evidence.get("reason"), "unknown")
            message_type = _text(evidence.get("message_type"), "OCPP evidence")
            lines.append(f"  unresolved {message_type}: {reason}")

    return "\n".join(lines)
