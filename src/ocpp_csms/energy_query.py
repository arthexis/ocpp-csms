from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ocpp_csms.schema import DATABASE_FILENAME

POWER_MEASURAND = "Power.Active.Import"
ENERGY_MEASURAND = "Energy.Active.Import.Register"


@dataclass(frozen=True)
class EnergySample:
    at: str
    charger_id: str
    connector_id: int | None
    transaction_id: int | None
    power_w: float | None
    energy_wh: float | None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalized_time(value: object) -> str | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def _number(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return result


def _power_w(value: object, unit: object) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if unit == "W":
        return number
    if unit == "kW":
        return number * 1000
    return None


def _energy_wh(value: object, unit: object) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if unit == "Wh":
        return number
    if unit == "kWh":
        return number * 1000
    return None


def _canonical_value(
    samples: list[dict[str, Any]],
    *,
    measurand: str,
    normalize,
) -> float | None:
    aggregate: float | None = None
    phases: dict[str, float] = {}

    for sample in samples:
        if sample.get("measurand") != measurand:
            continue
        value = normalize(sample.get("value"), sample.get("unit"))
        if value is None:
            continue
        phase = sample.get("phase")
        if isinstance(phase, str) and phase:
            phases[phase] = value
        else:
            aggregate = value

    if aggregate is not None:
        return aggregate
    if phases:
        return sum(phases.values())
    return None


def _entry_sample(
    *,
    charger_id: str,
    connector_id: int | None,
    transaction_id: int | None,
    entry: dict[str, Any],
) -> EnergySample | None:
    at = _normalized_time(entry.get("timestamp"))
    sampled = entry.get("sampled_value")
    if at is None or not isinstance(sampled, list):
        return None
    values = [item for item in sampled if isinstance(item, dict)]
    power = _canonical_value(values, measurand=POWER_MEASURAND, normalize=_power_w)
    energy = _canonical_value(values, measurand=ENERGY_MEASURAND, normalize=_energy_wh)
    if power is None and energy is None:
        return None
    return EnergySample(
        at=at,
        charger_id=charger_id,
        connector_id=connector_id,
        transaction_id=transaction_id,
        power_w=power,
        energy_wh=energy,
    )


class EnergyQuery:
    """Read and normalize MeterValues evidence without mutating appliance state."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.database = self.data_dir / DATABASE_FILENAME

    def samples(
        self,
        *,
        charger: str | None = None,
        connector: int | None = None,
        since: str | datetime | None = None,
        until: str | datetime | None = None,
    ) -> list[EnergySample]:
        if not self.database.exists():
            return []
        since_time = self._coerce_time(since)
        until_time = self._coerce_time(until)
        if since_time is not None and until_time is not None and since_time > until_time:
            raise ValueError("--since must not be later than --until")

        sql = """
            SELECT charger_id, transaction_id, payload_json
            FROM events
            WHERE direction = 'in' AND action = 'MeterValues'
        """
        parameters: list[object] = []
        if charger is not None:
            sql += " AND charger_id = ?"
            parameters.append(charger)
        sql += " ORDER BY id"

        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        try:
            rows = connection.execute(sql, parameters).fetchall()
        finally:
            connection.close()

        result: list[EnergySample] = []
        for charger_id, event_transaction_id, payload_json in rows:
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            payload_connector = payload.get("connector_id")
            try:
                connector_id = int(payload_connector) if payload_connector is not None else None
            except (TypeError, ValueError):
                connector_id = None
            if connector is not None and connector_id != connector:
                continue
            transaction_id = event_transaction_id
            if transaction_id is None:
                try:
                    transaction_id = int(payload["transaction_id"])
                except (KeyError, TypeError, ValueError):
                    transaction_id = None
            meter_values = payload.get("meter_value")
            if not isinstance(meter_values, list):
                continue
            for entry in meter_values:
                if not isinstance(entry, dict):
                    continue
                sample = _entry_sample(
                    charger_id=str(charger_id),
                    connector_id=connector_id,
                    transaction_id=int(transaction_id) if transaction_id is not None else None,
                    entry=entry,
                )
                if sample is None:
                    continue
                sample_time = _parse_time(sample.at)
                if sample_time is None:
                    continue
                if since_time is not None and sample_time < since_time:
                    continue
                if until_time is not None and sample_time > until_time:
                    continue
                result.append(sample)

        result.sort(key=lambda sample: (sample.at, sample.charger_id, sample.connector_id or -1))
        return result

    @staticmethod
    def _coerce_time(value: str | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        parsed = _parse_time(value)
        if parsed is None:
            raise ValueError(f"Invalid timestamp: {value}")
        return parsed

    def completed_summary(
        self,
        *,
        charger: str | None = None,
        connector: int | None = None,
        since: str | datetime | None = None,
        until: str | datetime | None = None,
    ) -> tuple[int, int]:
        """Return completed transaction count and meter-delta energy in Wh."""
        if not self.database.exists():
            return 0, 0
        since_time = self._coerce_time(since)
        until_time = self._coerce_time(until)
        if since_time is not None and until_time is not None and since_time > until_time:
            raise ValueError("--since must not be later than --until")

        sql = """
            SELECT charger_id, connector_id, meter_start, meter_stop,
                   COALESCE(stopped_at, stop_received_at, last_activity_at)
            FROM transactions
            WHERE meter_start IS NOT NULL AND meter_stop IS NOT NULL
        """
        parameters: list[object] = []
        if charger is not None:
            sql += " AND charger_id = ?"
            parameters.append(charger)
        if connector is not None:
            sql += " AND connector_id = ?"
            parameters.append(connector)

        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        try:
            rows = connection.execute(sql, parameters).fetchall()
        finally:
            connection.close()

        count = 0
        energy_wh = 0
        for _, _, meter_start, meter_stop, stopped_at in rows:
            timestamp = _parse_time(stopped_at)
            if timestamp is None:
                continue
            if since_time is not None and timestamp < since_time:
                continue
            if until_time is not None and timestamp > until_time:
                continue
            consumed = int(meter_stop) - int(meter_start)
            if consumed < 0:
                continue
            count += 1
            energy_wh += consumed
        return count, energy_wh
