from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from ocpp_csms.energy_query import EnergySample
from ocpp_csms.output import json_command_result

SCHEMA = "ocpp-csms/energy/v1"
DEFAULT_POWER_FRESHNESS_SECONDS = 120


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def current_power_w(
    samples: Iterable[EnergySample],
    *,
    at: datetime | None = None,
    freshness_seconds: int = DEFAULT_POWER_FRESHNESS_SECONDS,
) -> float | None:
    """Sum the latest fresh power sample for each charger/connector."""
    reference = at or datetime.now(timezone.utc)
    threshold = reference - timedelta(seconds=freshness_seconds)
    latest: dict[tuple[str, int | None], EnergySample] = {}

    for sample in samples:
        if sample.power_w is None:
            continue
        sampled_at = _parse_time(sample.at)
        if sampled_at < threshold or sampled_at > reference:
            continue
        key = (sample.charger_id, sample.connector_id)
        previous = latest.get(key)
        if previous is None or sample.at > previous.at:
            latest[key] = sample

    if not latest:
        return None
    return sum(float(sample.power_w) for sample in latest.values())


def energy_contract(
    samples: Iterable[EnergySample],
    *,
    completed_transactions: int,
    completed_energy_wh: int,
    power_reference_at: datetime | None = None,
) -> dict[str, object]:
    rows = list(samples)
    return json_command_result(
        {
            "summary": {
                "energy_wh": completed_energy_wh,
                "transactions": completed_transactions,
                "current_power_w": current_power_w(rows, at=power_reference_at),
                "power_freshness_seconds": DEFAULT_POWER_FRESHNESS_SECONDS,
                "sample_count": len(rows),
                "first_sample_at": rows[0].at if rows else None,
                "last_sample_at": rows[-1].at if rows else None,
            },
            "series": [
                {
                    "at": sample.at,
                    "charger_id": sample.charger_id,
                    "connector_id": sample.connector_id,
                    "transaction_id": sample.transaction_id,
                    "power_w": sample.power_w,
                    "energy_wh": sample.energy_wh,
                }
                for sample in rows
            ],
        },
        schema=SCHEMA,
    )
