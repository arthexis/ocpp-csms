from __future__ import annotations

from typing import Any

from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint
from ocpp.v16 import call_result

from ocpp_csms.time import utc_now_iso


class ChargePointSession(OcppChargePoint):
    """One OCPP 1.6J connection with deliberately direct, permissive handlers."""

    def __init__(self, charge_point_id: str, connection: Any) -> None:
        super().__init__(charge_point_id, connection)
        self._next_transaction_id = 1

    @on("BootNotification")
    async def on_boot_notification(self, **payload: Any) -> call_result.BootNotificationPayload:
        return call_result.BootNotificationPayload(
            current_time=utc_now_iso(),
            interval=60,
            status="Accepted",
        )

    @on("Heartbeat")
    async def on_heartbeat(self, **payload: Any) -> call_result.HeartbeatPayload:
        return call_result.HeartbeatPayload(current_time=utc_now_iso())

    @on("Authorize")
    async def on_authorize(self, **payload: Any) -> call_result.AuthorizePayload:
        return call_result.AuthorizePayload(id_tag_info={"status": "Accepted"})

    @on("StatusNotification")
    async def on_status_notification(self, **payload: Any) -> call_result.StatusNotificationPayload:
        return call_result.StatusNotificationPayload()

    @on("StartTransaction")
    async def on_start_transaction(self, **payload: Any) -> call_result.StartTransactionPayload:
        transaction_id = self._next_transaction_id
        self._next_transaction_id += 1
        return call_result.StartTransactionPayload(
            transaction_id=transaction_id,
            id_tag_info={"status": "Accepted"},
        )

    @on("StopTransaction")
    async def on_stop_transaction(self, **payload: Any) -> call_result.StopTransactionPayload:
        return call_result.StopTransactionPayload()

    @on("MeterValues")
    async def on_meter_values(self, **payload: Any) -> call_result.MeterValuesPayload:
        return call_result.MeterValuesPayload()
