from __future__ import annotations

from datetime import datetime, timezone
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



def _soc_endpoints(record: dict[str, Any]) -> tuple[int | None, int | None]:
    """Match txn CLI SoC semantics; never synthesize an unknown starting SoC."""
    from math import isfinite

    samples: list[int] = []
    payloads = record.get("meter_values")
    if not isinstance(payloads, list):
        return None, None
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for entry in payload.get("meter_value", []):
            if not isinstance(entry, dict):
                continue
            for sample in entry.get("sampled_value", []):
                if not isinstance(sample, dict):
                    continue
                if sample.get("measurand") != "SoC" or sample.get("unit", "Percent") != "Percent":
                    continue
                try:
                    number = float(sample["value"])
                except (KeyError, ValueError, TypeError, OverflowError):
                    continue
                if isfinite(number) and 0 <= number <= 100:
                    samples.append(round(number))
    if not samples:
        return None, None
    if len(samples) == 1:
        return None, samples[0]
    return samples[0], samples[-1]


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
    soc_start, soc_end = _soc_endpoints(record)
    status = record.get("status")
    missing_meter = start is None or stop is None or start.get("meter_start") is None or stop.get("meter_stop") is None
    return {
        "start_event_at": _utc(start.get("timestamp")) if start else None,
        "start_received_at": _utc(record.get("start_received_at")),
        "stop_event_at": _utc(stop.get("timestamp")) if stop else None,
        "stop_received_at": _utc(record.get("stop_received_at")),
        "meter_sample_times": samples,
        "soc_start": soc_start,
        "soc_end": soc_end,
        "quality": {
            "active": status == "open" and stop is None,
            "incomplete": start is None or stop is None,
            "missing_meter": missing_meter,
        },
    }
