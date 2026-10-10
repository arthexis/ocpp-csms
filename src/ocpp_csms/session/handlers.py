"""Incoming OCPP 1.6J handler methods, inherited by the concrete session."""
from __future__ import annotations

import logging
from typing import Any

from ocpp.routing import on
from ocpp.v16 import call_result
from ocpp_csms.time import utc_now_iso

# Preserve the existing operational logging namespace.
LOGGER = logging.getLogger("ocpp_csms.session")


class SessionHandlers:
    """OCPP incoming frame actions; state and recording live on ChargePointSession."""

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
        status = self._rfid_status(str(payload.get("id_tag", "")))
        return call_result.AuthorizePayload(id_tag_info={"status": status})

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
        authorization = self._rfid_status(str(payload.get("id_tag", "")))
        if authorization != "Accepted":
            self._record("StartTransaction", payload, transaction_id=0)
            return call_result.StartTransactionPayload(
                transaction_id=0,
                id_tag_info={"status": authorization},
            )

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
