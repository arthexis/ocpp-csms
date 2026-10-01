from __future__ import annotations

from itertools import count
from typing import Any

from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint

from ocpp_csms.time import utc_now_iso

_TRANSACTION_IDS = count(1)


class ChargePointSession(OcppChargePoint):
    """One OCPP 1.6J connection with deliberately direct, permissive handlers."""

    @on("BootNotification")
    async def on_boot_notification(self, **payload: Any) -> dict[str, Any]:
        return {
            "currentTime": utc_now_iso(),
            "interval": 60,
            "status": "Accepted",
        }

    @on("Heartbeat")
    async def on_heartbeat(self, **payload: Any) -> dict[str, Any]:
        return {"currentTime": utc_now_iso()}

    @on("Authorize")
    async def on_authorize(self, **payload: Any) -> dict[str, Any]:
        return {"idTagInfo": {"status": "Accepted"}}

    @on("StatusNotification")
    async def on_status_notification(self, **payload: Any) -> dict[str, Any]:
        return {}

    @on("StartTransaction")
    async def on_start_transaction(self, **payload: Any) -> dict[str, Any]:
        return {
            "transactionId": next(_TRANSACTION_IDS),
            "idTagInfo": {"status": "Accepted"},
        }

    @on("StopTransaction")
    async def on_stop_transaction(self, **payload: Any) -> dict[str, Any]:
        return {}

    @on("MeterValues")
    async def on_meter_values(self, **payload: Any) -> dict[str, Any]:
        return {}
