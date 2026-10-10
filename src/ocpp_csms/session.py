from __future__ import annotations

import logging
from typing import Any

from ocpp.messages import Call
from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint
from ocpp.v16 import call, call_result

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.rfid.authorization import load_rfid_authorization
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

    async def remote_start(
        self,
        *,
        id_tag: str,
        connector_id: int | None = None,
    ) -> call_result.RemoteStartTransactionPayload:
        payload: dict[str, Any] = {"id_tag": id_tag}
        if connector_id is not None:
            payload["connector_id"] = connector_id
        self._record("RemoteStartTransaction", payload, direction="out")
        response = await self.call(
            call.RemoteStartTransactionPayload(
                id_tag=id_tag,
                connector_id=connector_id,
            )
        )
        self._record("RemoteStartTransaction", dict(response.__dict__), direction="in")
        return response

    async def remote_stop(self, transaction_id: int) -> call_result.RemoteStopTransactionPayload:
        payload = {"transaction_id": transaction_id}
        self._record(
            "RemoteStopTransaction",
            payload,
            direction="out",
            transaction_id=transaction_id,
        )
        response = await self.call(call.RemoteStopTransactionPayload(transaction_id=transaction_id))
        self._record(
            "RemoteStopTransaction",
            dict(response.__dict__),
            direction="in",
            transaction_id=transaction_id,
        )
        return response

    async def reset(self, reset_type: str = "Soft") -> call_result.ResetPayload:
        payload = {"type": reset_type}
        self._record("Reset", payload, direction="out")
        response = await self.call(call.ResetPayload(type=reset_type))
        self._record("Reset", dict(response.__dict__), direction="in")
        return response

    async def get_configuration(
        self,
        keys: list[str] | None = None,
    ) -> call_result.GetConfigurationPayload:
        payload: dict[str, Any] = {}
        if keys:
            payload["key"] = list(keys)
        self._record("GetConfiguration", payload, direction="out")
        response = await self.call(call.GetConfigurationPayload(key=keys))
        self._record("GetConfiguration", dict(response.__dict__), direction="in")
        return response

    async def change_configuration(
        self,
        key: str,
        value: str,
    ) -> call_result.ChangeConfigurationPayload:
        payload = {"key": key, "value": value}
        self._record("ChangeConfiguration", payload, direction="out")
        response = await self.call(call.ChangeConfigurationPayload(key=key, value=value))
        self._record("ChangeConfiguration", dict(response.__dict__), direction="in")
        return response

    async def get_local_list_version(self) -> call_result.GetLocalListVersionPayload:
        self._record("GetLocalListVersion", {}, direction="out")
        response = await self.call(call.GetLocalListVersionPayload())
        self._record("GetLocalListVersion", dict(response.__dict__), direction="in")
        return response

    async def send_local_list(
        self,
        list_version: int,
        entries: list[dict[str, Any]],
    ) -> call_result.SendLocalListPayload:
        local_authorization_list = [
            {
                "id_tag": str(entry["rfid"]),
                "id_tag_info": {"status": "Accepted"},
            }
            for entry in entries
        ]
        payload: dict[str, Any] = {
            "list_version": list_version,
            "update_type": "Full",
        }
        if local_authorization_list:
            payload["local_authorization_list"] = local_authorization_list
        self._record("SendLocalList", payload, direction="out")
        response = await self.call(
            call.SendLocalListPayload(
                list_version=list_version,
                update_type="Full",
                local_authorization_list=local_authorization_list or None,
            )
        )
        self._record("SendLocalList", dict(response.__dict__), direction="in")
        return response

    async def set_charging_profile(
        self,
        connector_id: int,
        profile: dict[str, Any],
    ) -> call_result.SetChargingProfilePayload:
        payload = {"connector_id": connector_id, "cs_charging_profiles": profile}
        self._record("SetChargingProfile", payload, direction="out")
        response = await self.call(
            call.SetChargingProfilePayload(
                connector_id=connector_id,
                cs_charging_profiles=profile,
            )
        )
        self._record("SetChargingProfile", dict(response.__dict__), direction="in")
        return response

    async def clear_charging_profile(
        self,
        *,
        profile_id: int | None = None,
        connector_id: int | None = None,
        purpose: str | None = None,
        stack_level: int | None = None,
    ) -> call_result.ClearChargingProfilePayload:
        payload: dict[str, Any] = {}
        if profile_id is not None:
            payload["id"] = profile_id
        if connector_id is not None:
            payload["connector_id"] = connector_id
        if purpose is not None:
            payload["charging_profile_purpose"] = purpose
        if stack_level is not None:
            payload["stack_level"] = stack_level
        self._record("ClearChargingProfile", payload, direction="out")
        response = await self.call(
            call.ClearChargingProfilePayload(
                id=profile_id,
                connector_id=connector_id,
                charging_profile_purpose=purpose,
                stack_level=stack_level,
            )
        )
        self._record("ClearChargingProfile", dict(response.__dict__), direction="in")
        return response

    async def get_composite_schedule(
        self,
        connector_id: int,
        duration: int,
        charging_rate_unit: str | None = None,
    ) -> call_result.GetCompositeSchedulePayload:
        payload: dict[str, Any] = {"connector_id": connector_id, "duration": duration}
        if charging_rate_unit is not None:
            payload["charging_rate_unit"] = charging_rate_unit
        self._record("GetCompositeSchedule", payload, direction="out")
        response = await self.call(
            call.GetCompositeSchedulePayload(
                connector_id=connector_id,
                duration=duration,
                charging_rate_unit=charging_rate_unit,
            )
        )
        self._record("GetCompositeSchedule", dict(response.__dict__), direction="in")
        return response

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
