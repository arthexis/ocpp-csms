from __future__ import annotations

from typing import Any

from ocpp_csms.time import utc_now_iso


class HeartbeatHandler:
    async def handle(self, charge_point_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"currentTime": utc_now_iso()}
