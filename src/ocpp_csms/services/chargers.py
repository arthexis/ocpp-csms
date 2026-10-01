from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Charger:
    charge_point_id: str
    accepted: bool = True
    heartbeat_interval: int = 60
    boot_payload: dict[str, Any] = field(default_factory=dict)
    status_payload: dict[str, Any] = field(default_factory=dict)


class ChargerService:
    def __init__(self) -> None:
        self._chargers: dict[str, Charger] = {}

    async def register_boot(self, charge_point_id: str, payload: dict[str, Any]) -> Charger:
        charger = self._chargers.setdefault(charge_point_id, Charger(charge_point_id))
        charger.boot_payload = payload
        return charger

    async def record_status(self, charge_point_id: str, payload: dict[str, Any]) -> None:
        charger = self._chargers.setdefault(charge_point_id, Charger(charge_point_id))
        charger.status_payload = payload
