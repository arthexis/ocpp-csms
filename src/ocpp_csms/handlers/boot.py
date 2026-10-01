from __future__ import annotations

from typing import Any

from ocpp_csms.services.chargers import ChargerService
from ocpp_csms.time import utc_now_iso


class BootNotificationHandler:
    def __init__(self, chargers: ChargerService) -> None:
        self.chargers = chargers

    async def handle(self, charge_point_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        charger = await self.chargers.register_boot(charge_point_id, payload)
        return {
            "status": "Accepted" if charger.accepted else "Rejected",
            "currentTime": utc_now_iso(),
            "interval": charger.heartbeat_interval,
        }
