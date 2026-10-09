from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ocpp_csms.transaction_query import TransactionView


def _text(value: object | None, default: str = "-") -> str:
    return default if value is None or value == "" else str(value)


def _timestamp(value: object | None) -> str:
    return value if isinstance(value, str) else "-"


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


def transaction_energy_wh(view: TransactionView) -> int | None:
    """Return reliable start-to-stop energy for a transaction, when available."""
    record = view.record
    start = record.get("start") if isinstance(record.get("start"), dict) else {}
    stop = record.get("stop") if isinstance(record.get("stop"), dict) else {}
    return _energy_wh(start, stop)


def _live_energy_wh(view: TransactionView) -> int | None:
    """Estimate delivered energy from the newest valid cumulative meter sample.

    A running transaction has no meter_stop yet. Only use the cumulative
    Energy.Active.Import.Register measurand, not instantaneous power or
    an unrelated sample. Negative deltas are not trustworthy.
    """
    record = view.record
    start = record.get("start")
    if not isinstance(start, dict):
        return None
    try:
        meter_start = int(start["meter_start"])
    except (KeyError, ValueError, TypeError):
        return None
    payloads = record.get("meter_values")
    if not isinstance(payloads, list):
        return None
    for payload in reversed(payloads):
        if not isinstance(payload, dict):
            continue
        entries = payload.get("meter_value")
        if not isinstance(entries, list):
            continue
        for entry in reversed(entries):
            if not isinstance(entry, dict):
                continue
            samples = entry.get("sampled_value")
            if not isinstance(samples, list):
                continue
            for sample in reversed(samples):
                if not isinstance(sample, dict) or sample.get("measurand") != "Energy.Active.Import.Register":
                    continue
                try:
                    value = float(sample["value"])
                    unit = sample.get("unit", "Wh")
                    if unit == "kWh":
                        value *= 1000
                    elif unit != "Wh":
                        continue
                    delta = value - meter_start
                    if 0 <= delta < float("inf"):
                        return round(delta)
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
    return None


def _format_energy(energy_wh: int) -> str:
    return f"{energy_wh / 1000:.3f} kWh" if energy_wh >= 1000 else f"{energy_wh} Wh"


def _transaction_energy(view: TransactionView) -> str:
    energy_wh = transaction_energy_wh(view)
    if energy_wh is None:
        energy_wh = _live_energy_wh(view)
    return _format_energy(energy_wh) if energy_wh is not None else "-"


def _meter_summary(record: dict[str, Any], *, local_time: bool = False) -> dict[str, object]:
    payloads = record.get("meter_values")
    if not isinstance(payloads, list):
        payloads = []
    received = record.get("meter_values_received_at")
    if not isinstance(received, list):
        received = []
    sample_count = 0
    timestamps: list[str] = []
    latest_energy: tuple[str, str] | None = None
    latest_power: tuple[str, str] | None = None
    for index, payload in enumerate(payloads):
        if not isinstance(payload, dict):
            continue
        if local_time and index < len(received) and isinstance(received[index], str):
            timestamps.append(received[index])
        meter_values = payload.get("meter_value")
        if not isinstance(meter_values, list):
            continue
        for entry in meter_values:
            if not isinstance(entry, dict):
                continue
            if not local_time and isinstance(entry.get("timestamp"), str):
                timestamps.append(entry["timestamp"])
            samples = entry.get("sampled_value")
            if not isinstance(samples, list):
                continue
            for sample in samples:
                if not isinstance(sample, dict):
                    continue
                sample_count += 1
                value, measurand, unit = sample.get("value"), sample.get("measurand"), sample.get("unit")
                if not all(isinstance(item, str) for item in (value, measurand, unit)):
                    continue
                if measurand == "Energy.Active.Import.Register":
                    latest_energy = (value, unit)
                elif measurand == "Power.Active.Import":
                    latest_power = (value, unit)
    return {"messages": len(payloads), "samples": sample_count, "first": min(timestamps) if timestamps else None, "last": max(timestamps) if timestamps else None, "latest_energy": latest_energy, "latest_power": latest_power}


def format_transactions(views: Iterable[TransactionView], *, local_time: bool = False) -> str:
    rows = list(views)
    if not rows:
        return "No transactions."
    headers = ("TXN", "STATUS", "CHARGER", "C", "RFID", "ENERGY", "EVENT TIME")
    body = [
        (str(view.transaction_id), view.status or "unknown", view.charge_point_id or "-", _text(view.connector_id, "?"), _text(view.id_tag), _transaction_energy(view), _age_key(view.event_time(local_time=local_time)))
        for view in rows
    ]
    widths = [max(len(headers[index]), *(len(row[index]) for row in body)) for index in range(len(headers))]
    def line(values: tuple[str, ...]) -> str:
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(values)).rstrip()
    return "\n".join([line(headers), *(line(row) for row in body)])


def format_transaction(view: TransactionView, *, local_time: bool = False) -> str:
    record = view.record
    start = record.get("start") if isinstance(record.get("start"), dict) else {}
    stop = record.get("stop") if isinstance(record.get("stop"), dict) else {}
    origin = record.get("origin") or ("local" if start else "recovered")
    if local_time:
        started = record.get("start_received_at") or (record.get("created_at") if start else None)
        stopped = record.get("stop_received_at") or (record.get("updated_at") if stop else None)
    else:
        started, stopped = start.get("timestamp"), stop.get("timestamp")
    duration = _duration(started, stopped)
    energy_wh = transaction_energy_wh(view)
    estimated = energy_wh is None
    if estimated:
        energy_wh = _live_energy_wh(view)
    meter = _meter_summary(record, local_time=local_time)
    lines = [
        f"Transaction {view.transaction_id}", f"Status:       {view.status or 'unknown'}", f"Origin:       {origin}",
        f"Charger:      {view.charge_point_id or '-'}", f"Connector:    {_text(view.connector_id, '?')}", f"RFID:         {_text(view.id_tag)}", "",
        f"Started:      {_timestamp(started)}", f"Stopped:      {_timestamp(stopped)}",
    ]
    if duration is not None:
        lines.append(f"Duration:     {duration}")
    lines.extend((f"Event time:   {_age_key(view.event_time(local_time=local_time))}", f"Meter start:  {_text(start.get('meter_start'))}", f"Meter stop:   {_text(stop.get('meter_stop'))}"))
    if energy_wh is not None:
        lines.append(f"Energy:       {_format_energy(energy_wh)}" + (" (live)" if estimated else ""))
    lines.extend(("", "MeterValues:", f"  messages:   {meter['messages']}", f"  samples:    {meter['samples']}", f"  first:      {_text(meter['first'])}", f"  last:       {_text(meter['last'])}"))
    if meter["latest_energy"] is not None:
        value, unit = meter["latest_energy"]
        lines.append(f"  latest energy: {value} {unit}")
    if meter["latest_power"] is not None:
        value, unit = meter["latest_power"]
        lines.append(f"  latest power: {value} {unit}")
    if origin == "recovered" and not start:
        recovered_by = "StopTransaction" if stop else "MeterValues" if record.get("meter_values") else "historical traffic"
        lines.extend(("", "Recovery:", "  start:      unknown", f"  recovered:  {recovered_by}"))
    if view.unresolved:
        lines.extend(("", "Warnings:"))
        for unresolved in view.unresolved:
            lines.append(f"  unresolved {unresolved.get('message_type') or 'OCPP'}: {unresolved.get('reason') or 'unknown'}")
    lines.extend(("", f"Archive:      {_archive_path(view)}"))
    return "\n".join(lines)
