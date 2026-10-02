from __future__ import annotations

import logging
from typing import Any

from ocpp.routing import after, on
from ocpp.v16 import ChargePoint as OcppChargePoint
from ocpp.v16 import call_result

from ocpp_csms.events import EventStore
from ocpp_csms.time import utc_now_iso
from ocpp_csms.transactions import TransactionArchive

LOGGER = logging.getLogger(__name__)


class ChargePointSession(OcppChargePoint):
    """One OCPP 1.6J connection with deliberately direct, permissive handlers."""

    def __init__(
        self,
        charge_point_id: str,
        connection: Any,
        transactions: TransactionArchive,
        events: EventStore,
    ) -> None:
        super().__init__(charge_point_id, connection)
        self.transactions = transactions
        self.events = events
        self.connection = connection

    def _record(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        direction: str = "in",
        transaction_id: int | None = None,
    ) -> None:
        try:
            self.events.record_ocpp(
                self.id,
                action,
                payload,
                direction=direction,
                transaction_id=transaction_id,
            )
        except Exception:
            LOGGER.exception("frame %s", self.connection.last_frame)

    def _record_response(
        self,
        action: str,
        response: dict[str, Any],
        *,
        transaction_id: int | None = None,
    ) -> None:
        self._record(
            action,
            response,
            direction="out",
            transaction_id=transaction_id,
        )

    @on("BootNotification")
    async def on_boot_notification(self, **payload: Any) -> call_result.BootNotificationPayload:
        self._record("BootNotification", payload)
        return call_result.BootNotificationPayload(
            current_time=utc_now_iso(),
            interval=60,
            status="Accepted",
        )

    @after("BootNotification", inject_response=True)
    def after_boot_notification(self, call_response: dict[str, Any], **_: Any) -> None:
        self._record_response("BootNotification", call_response)

    @on("Heartbeat")
    async def on_heartbeat(self, **payload: Any) -> call_result.HeartbeatPayload:
        self._record("Heartbeat", payload)
        return call_result.HeartbeatPayload(current_time=utc_now_iso())

    @after("Heartbeat", inject_response=True)
    def after_heartbeat(self, call_response: dict[str, Any], **_: Any) -> None:
        self._record_response("Heartbeat", call_response)

    @on("Authorize")
    async def on_authorize(self, **payload: Any) -> call_result.AuthorizePayload:
        self._record("Authorize", payload)
        return call_result.AuthorizePayload(id_tag_info={"status": "Accepted"})

    @after("Authorize", inject_response=True)
    def after_authorize(self, call_response: dict[str, Any], **_: Any) -> None:
        self._record_response("Authorize", call_response)

    @on("StatusNotification")
    async def on_status_notification(self, **payload: Any) -> call_result.StatusNotificationPayload:
        self._record("StatusNotification", payload)
        try:
            accepted = self.events.record_connector_status(self.id, payload)
            if not accepted:
                LOGGER.info(
                    "ignored status %s connector %s",
                    payload.get("status"),
                    payload.get("connector_id"),
                )
        except Exception:
            LOGGER.exception("frame %s", self.connection.last_frame)
        return call_result.StatusNotificationPayload()

    @after("StatusNotification", inject_response=True)
    def after_status_notification(self, call_response: dict[str, Any], **_: Any) -> None:
        self._record_response("StatusNotification", call_response)

    @on("StartTransaction")
    async def on_start_transaction(self, **payload: Any) -> call_result.StartTransactionPayload:
        transaction_id = None
        try:
            transaction_id = self.events.find_recent_start(self.id, payload)
        except Exception:
            LOGGER.exception("frame %s", self.connection.last_frame)

        if transaction_id is None:
            transaction_id = await self.transactions.start(self.id, payload)
            try:
                self.events.record_transaction_start(transaction_id, self.id, payload)
            except Exception:
                LOGGER.exception("frame %s", self.connection.last_frame)
        else:
            LOGGER.info("deduplicated StartTransaction %s", transaction_id)

        self._record("StartTransaction", payload, transaction_id=transaction_id)
        return call_result.StartTransactionPayload(
            transaction_id=transaction_id,
            id_tag_info={"status": "Accepted"},
        )

    @after("StartTransaction", inject_response=True)
    def after_start_transaction(self, call_response: dict[str, Any], **_: Any) -> None:
        self._record_response(
            "StartTransaction",
            call_response,
            transaction_id=int(call_response["transaction_id"]),
        )

    @on("StopTransaction")
    async def on_stop_transaction(self, **payload: Any) -> call_result.StopTransactionPayload:
        self._record("StopTransaction", payload)
        try:
            self.events.record_transaction_stop(self.id, payload)
        except Exception:
            LOGGER.exception("frame %s", self.connection.last_frame)
        try:
            await self.transactions.stop(self.id, payload)
        except Exception:
            LOGGER.exception("Could not update transaction JSON for %s", self.id)
        return call_result.StopTransactionPayload()

    @after("StopTransaction", inject_response=True)
    def after_stop_transaction(self, call_response: dict[str, Any], **_: Any) -> None:
        self._record_response("StopTransaction", call_response)

    @on("MeterValues")
    async def on_meter_values(self, **payload: Any) -> call_result.MeterValuesPayload:
        self._record("MeterValues", payload)
        transaction_id = payload.get("transaction_id")
        if transaction_id is not None:
            try:
                self.events.record_transaction_activity(int(transaction_id))
            except Exception:
                LOGGER.exception("frame %s", self.connection.last_frame)
        try:
            await self.transactions.meter_values(self.id, payload)
        except Exception:
            LOGGER.exception("Could not update transaction JSON for %s", self.id)
        return call_result.MeterValuesPayload()

    @after("MeterValues", inject_response=True)
    def after_meter_values(self, call_response: dict[str, Any], **_: Any) -> None:
        self._record_response("MeterValues", call_response)
