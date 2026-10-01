from __future__ import annotations

from typing import Any

from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint

from ocpp_csms.routing import HandlerRegistry


class ChargePointSession(OcppChargePoint):
    def __init__(self, charge_point_id: str, connection: Any, registry: HandlerRegistry) -> None:
        super().__init__(charge_point_id, connection)
        self.registry = registry

    @on("BootNotification")
    async def on_boot_notification(self, **payload: Any) -> dict[str, Any]:
        return await self.registry.handle("BootNotification", self.id, payload)

    @on("Heartbeat")
    async def on_heartbeat(self, **payload: Any) -> dict[str, Any]:
        return await self.registry.handle("Heartbeat", self.id, payload)

    @on("Authorize")
    async def on_authorize(self, **payload: Any) -> dict[str, Any]:
        return await self.registry.handle("Authorize", self.id, payload)

    @on("StatusNotification")
    async def on_status_notification(self, **payload: Any) -> dict[str, Any]:
        return await self.registry.handle("StatusNotification", self.id, payload)

    @on("StartTransaction")
    async def on_start_transaction(self, **payload: Any) -> dict[str, Any]:
        return await self.registry.handle("StartTransaction", self.id, payload)

    @on("StopTransaction")
    async def on_stop_transaction(self, **payload: Any) -> dict[str, Any]:
        return await self.registry.handle("StopTransaction", self.id, payload)

    @on("MeterValues")
    async def on_meter_values(self, **payload: Any) -> dict[str, Any]:
        return await self.registry.handle("MeterValues", self.id, payload)
