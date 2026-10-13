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
    recovered = record.get("origin") == "recovered" or start is None
    inferred = status == "inferred_stopped"
    missing_meter = start is None or stop is None or start.get("meter_start") is None or stop.get("meter_stop") is None
    return {
        "start_event_at": _utc(start.get("timestamp")) if start else None,
        "start_received_at": _utc(record.get("start_received_at")),
        "stop_event_at": _utc(stop.get("timestamp")) if stop else None,
        "stop_received_at": _utc(record.get("stop_received_at")),
        "meter_sample_times": samples,
        "quality": {
            "active": status in {"open", "recovered"} and stop is None,
            "incomplete": start is None or stop is None,
            "missing_meter": missing_meter,
            "recovered": recovered,
            "inferred": inferred,
            "corrected": isinstance(record.get("recovery"), dict)
                         and record["recovery"].get("reconciled_at") is not None,
        },
    }
