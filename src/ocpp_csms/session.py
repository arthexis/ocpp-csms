from __future__ import annotations

import logging
from typing import Any

from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint

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

    def _reply(
        self,
        action: str,
        response: dict[str, Any],
        *,
        transaction_id: int | None = None,
    ) -> dict[str, Any]:
        self._record(
            action,
            response,
            direction="out",
            transaction_id=transaction_id,
        )
        return response

    @on("BootNotification")
    async def on_boot_notification(self, **payload: Any) -> dict[str, Any]:
        self._record("BootNotification", payload)
        return self._reply(
            "BootNotification",
            {
                "currentTime": utc_now_iso(),
                "interval": 60,
                "status": "Accepted",
            },
        )

    @on("Heartbeat")
    async def on_heartbeat(self, **payload: Any) -> dict[str, Any]:
        self._record("Heartbeat", payload)
        return self._reply("Heartbeat", {"currentTime": utc_now_iso()})

    @on("Authorize")
    async def on_authorize(self, **payload: Any) -> dict[str, Any]:
        self._record("Authorize", payload)
        return self._reply("Authorize", {"idTagInfo": {"status": "Accepted"}})

    @on("StatusNotification")
    async def on_status_notification(self, **payload: Any) -> dict[str, Any]:
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
        return self._reply("StatusNotification", {})

    @on("StartTransaction")
    async def on_start_transaction(self, **payload: Any) -> dict[str, Any]:
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
        return self._reply(
            "StartTransaction",
            {
                "transactionId": transaction_id,
                "idTagInfo": {"status": "Accepted"},
            },
            transaction_id=transaction_id,
        )

    @on("StopTransaction")
    async def on_stop_transaction(self, **payload: Any) -> dict[str, Any]:
        self._record("StopTransaction", payload)
        try:
            self.events.record_transaction_stop(self.id, payload)
        except Exception:
            LOGGER.exception("frame %s", self.connection.last_frame)
        try:
            await self.transactions.stop(self.id, payload)
        except Exception:
            LOGGER.exception("Could not update transaction JSON for %s", self.id)
        return self._reply("StopTransaction", {})

    @on("MeterValues")
    async def on_meter_values(self, **payload: Any) -> dict[str, Any]:
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
        return self._reply("MeterValues", {})
