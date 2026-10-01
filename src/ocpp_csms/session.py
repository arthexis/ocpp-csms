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

    def _record(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        transaction_id: int | None = None,
    ) -> None:
        try:
            self.events.record_ocpp(
                self.id,
                action,
                payload,
                transaction_id=transaction_id,
            )
        except Exception:
            LOGGER.exception("Could not persist %s event for %s", action, self.id)

    @on("BootNotification")
    async def on_boot_notification(self, **payload: Any) -> dict[str, Any]:
        self._record("BootNotification", payload)
        return {
            "currentTime": utc_now_iso(),
            "interval": 60,
            "status": "Accepted",
        }

    @on("Heartbeat")
    async def on_heartbeat(self, **payload: Any) -> dict[str, Any]:
        self._record("Heartbeat", payload)
        return {"currentTime": utc_now_iso()}

    @on("Authorize")
    async def on_authorize(self, **payload: Any) -> dict[str, Any]:
        self._record("Authorize", payload)
        return {"idTagInfo": {"status": "Accepted"}}

    @on("StatusNotification")
    async def on_status_notification(self, **payload: Any) -> dict[str, Any]:
        self._record("StatusNotification", payload)
        return {}

    @on("StartTransaction")
    async def on_start_transaction(self, **payload: Any) -> dict[str, Any]:
        transaction_id = await self.transactions.start(self.id, payload)
        self._record("StartTransaction", payload, transaction_id=transaction_id)
        return {
            "transactionId": transaction_id,
            "idTagInfo": {"status": "Accepted"},
        }

    @on("StopTransaction")
    async def on_stop_transaction(self, **payload: Any) -> dict[str, Any]:
        self._record("StopTransaction", payload)
        try:
            await self.transactions.stop(self.id, payload)
        except Exception:
            LOGGER.exception("Could not update transaction JSON for %s", self.id)
        return {}

    @on("MeterValues")
    async def on_meter_values(self, **payload: Any) -> dict[str, Any]:
        self._record("MeterValues", payload)
        try:
            await self.transactions.meter_values(self.id, payload)
        except Exception:
            LOGGER.exception("Could not update transaction JSON for %s", self.id)
        return {}
