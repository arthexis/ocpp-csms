from __future__ import annotations

import logging
from typing import Any

from ocpp.messages import Call
from ocpp.routing import on
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

    def _record_recovery_decision(self, decision: dict[str, Any] | None) -> None:
        if decision is None:
            return
        event = str(decision["event"])
        details = {key: value for key, value in decision.items() if key != "event"}
        try:
            self.events.record_runtime(event, charger_id=self.id, details=details)
            if event == "historical_transaction_id_collision":
                self.events.record_runtime(
                    "unresolved_queued_message_preserved",
                    charger_id=self.id,
                    details=details,
                )
        except Exception:
            LOGGER.exception("frame %s", self.connection.last_frame)

    async def _handle_call(self, msg: Call):
        response = await super()._handle_call(msg)
        if response is None:
            return None

        transaction_id = None
        if msg.action == "StartTransaction":
            transaction_id = int(response.payload["transactionId"])

        self._record_response(
            msg.action,
            response.payload,
            transaction_id=transaction_id,
        )
        return response

    @on("BootNotification")
    async def on_boot_notification(self, **payload: Any) -> call_result.BootNotificationPayload:
        self._record("BootNotification", payload)
        return call_result.BootNotificationPayload(
            current_time=utc_now_iso(),
            interval=60,
            status="Accepted",
        )

    @on("Heartbeat")
    async def on_heartbeat(self, **payload: Any) -> call_result.HeartbeatPayload:
        self._record("Heartbeat", payload)
        return call_result.HeartbeatPayload(current_time=utc_now_iso())

    @on("Authorize")
    async def on_authorize(self, **payload: Any) -> call_result.AuthorizePayload:
        self._record("Authorize", payload)
        return call_result.AuthorizePayload(id_tag_info={"status": "Accepted"})

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

    @on("StopTransaction")
    async def on_stop_transaction(self, **payload: Any) -> call_result.StopTransactionPayload:
        self._record("StopTransaction", payload)
        decision = None
        try:
            decision = await self.transactions.stop(self.id, payload)
        except Exception:
            LOGGER.exception("Could not update transaction JSON for %s", self.id)
        self._record_recovery_decision(decision)
        if decision is None or decision.get("event") != "historical_transaction_id_collision":
            try:
                self.events.record_transaction_stop(self.id, payload)
            except Exception:
                LOGGER.exception("frame %s", self.connection.last_frame)
        return call_result.StopTransactionPayload()

    @on("MeterValues")
    async def on_meter_values(self, **payload: Any) -> call_result.MeterValuesPayload:
        self._record("MeterValues", payload)
        decision = None
        try:
            decision = await self.transactions.meter_values(self.id, payload)
        except Exception:
            LOGGER.exception("Could not update transaction JSON for %s", self.id)
        self._record_recovery_decision(decision)
        transaction_id = payload.get("transaction_id")
        if (
            transaction_id is not None
            and (decision is None or decision.get("event") != "historical_transaction_id_collision")
        ):
            try:
                self.events.record_transaction_activity(int(transaction_id))
            except Exception:
                LOGGER.exception("frame %s", self.connection.last_frame)
        return call_result.MeterValuesPayload()
