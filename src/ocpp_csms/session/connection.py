from __future__ import annotations

import logging
from typing import Any

from ocpp.messages import Call
from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint
from ocpp.v16 import call, call_result

from .commands import SessionCommands
from .handlers import SessionHandlers
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.rfid.authorization import load_rfid_authorization
from ocpp_csms.time import utc_now_iso
from ocpp_csms.transactions.archive import TransactionArchive

LOGGER = logging.getLogger(__name__)


class ChargePointSession(SessionCommands, SessionHandlers, OcppChargePoint):
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

    def _rfid_status(self, id_tag: str) -> str:
        policy = load_rfid_authorization(self.transactions.data_dir)
        if not policy.valid:
            LOGGER.error(
                "RFID authorization file %s is invalid: %s",
                policy.source,
                policy.error,
            )
        return policy.status(id_tag)

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

