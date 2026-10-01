from __future__ import annotations

from typing import Any

from ocpp_csms.services.chargers import ChargerService


class StatusNotificationHandler:
    def __init__(self, chargers: ChargerService) -> None:
        self.chargers = chargers

    async def handle(self, charge_point_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self.chargers.record_status(charge_point_id, payload)
        return {}
