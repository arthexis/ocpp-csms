"""Pure human-readable fault rendering; transport and SQLite queries stay in diagnostics."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def render_faults(faults: list[dict[str, Any]], *, event_display_timestamp: Callable[..., str], local_time: bool = False) -> str:
    if not faults:
        return "No recorded fault notifications."
    lines = []
    for fault in faults:
        connector = fault["connector_id"]
        parts = [event_display_timestamp(str(fault["received_at"]), local_time=local_time), str(fault["charger_id"]),
                 f"C{connector}" if connector is not None else "C?",
                 "Faulted", str(fault.get("error_code") or "UnknownError")]
        for name in ("charger_at", "info", "vendor_id", "vendor_error_code"):
            if fault.get(name) not in (None, ""):
                parts.append(f"{name}={event_display_timestamp(str(fault[name]), local_time=local_time) if name == 'charger_at' else fault[name]}")
        lines.append("  ".join(parts))
    return "\n".join(lines)


