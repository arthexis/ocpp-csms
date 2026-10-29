from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any


def _utc(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")



def _live_energy(record: dict[str, Any]) -> dict[str, Any]:
    """Provisional register delta from evidence attached to this transaction."""
    result: dict[str, Any] = {
        "live_energy_wh": None,
        "live_energy_observed_at": None,
        "live_energy_reason": "missing_start",
        "live_energy_measurand": "Energy.Active.Import.Register",
    }
    start = record.get("start")
    if not isinstance(start, dict) or start.get("meter_start") is None:
        return result
    try:
        initial = float(start["meter_start"])
        if not isfinite(initial) or initial < 0:
            return result
    except (TypeError, ValueError, OverflowError):
        return result
    result["live_energy_reason"] = "missing_sample"
    latest: tuple[datetime, int] | None = None
    payloads = record.get("meter_values")
    if not isinstance(payloads, list):
        return result
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        entries = payload.get("meter_value")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            timestamp = _utc(entry.get("timestamp"))
            if timestamp is None:
                continue
            observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            samples = entry.get("sampled_value")
            if not isinstance(samples, list):
                continue
            for sample in samples:
                if not isinstance(sample, dict):
                    continue
                if sample.get("measurand") != "Energy.Active.Import.Register":
                    continue
                # Sampled values with a non-register context cannot be used.
                if sample.get("context") in ("Transaction.Begin", "Transaction.End"):
                    continue
                unit = sample.get("unit", "Wh")
                if unit not in ("Wh", "kWh"):
                    continue
                try:
                    value = float(sample["value"]) * (1000 if unit == "kWh" else 1)
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                if not isfinite(value) or value < initial:
                    continue
                if latest is None or observed > latest[0]:
                    latest = (observed, round(value - initial))
    if latest is not None:
        result.update(live_energy_wh=latest[1],
                      live_energy_observed_at=latest[0].isoformat().replace("+00:00", "Z"),
                      live_energy_reason=None)
    return result

def transaction_export_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Additive evidence metadata for export, without changing CLI transaction semantics."""
    start = record.get("start") if isinstance(record.get("start"), dict) else None
    stop = record.get("stop") if isinstance(record.get("stop"), dict) else None
    meter_values = record.get("meter_values")
    received_times = record.get("meter_values_received_at")
    samples: list[dict[str, Any]] = []
    if isinstance(meter_values, list):
        for index, payload in enumerate(meter_values):
            received = received_times[index] if isinstance(received_times, list) and index < len(received_times) else None
            entries = payload.get("meter_value") if isinstance(payload, dict) else None
            if isinstance(entries, list):
                for sample_index, entry in enumerate(entries):
                    samples.append({
                        "payload_index": index,
                        "sample_index": sample_index,
                        "event_at": _utc(entry.get("timestamp")) if isinstance(entry, dict) else None,
                        "received_at": _utc(received),
                    })
    status = record.get("status")
    missing_meter = start is None or stop is None or start.get("meter_start") is None or stop.get("meter_stop") is None
    return {
        **(_live_energy(record) if status == "open" and stop is None else {}),
        "start_event_at": _utc(start.get("timestamp")) if start else None,
        "start_received_at": _utc(record.get("start_received_at")),
        "stop_event_at": _utc(stop.get("timestamp")) if stop else None,
        "stop_received_at": _utc(record.get("stop_received_at")),
        "meter_sample_times": samples,
        "quality": {
            "active": status == "open" and stop is None,
            "incomplete": start is None or stop is None,
            "missing_meter": missing_meter,
        },
    }
