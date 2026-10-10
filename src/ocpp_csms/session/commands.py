"""Outgoing OCPP 1.6J commands shared by a charge-point session."""
from __future__ import annotations

from typing import Any
from ocpp.v16 import call, call_result


class SessionCommands:
    """Send charger commands and record both directions via the concrete session."""

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


    async def trigger_message(self, requested_message: str, connector_id: int | None = None) -> call_result.TriggerMessagePayload:
        payload: dict[str, Any] = {"requested_message": requested_message}
        if connector_id is not None:
            payload["connector_id"] = connector_id
        self._record("TriggerMessage", payload, direction="out")
        response = await self.call(call.TriggerMessagePayload(requested_message=requested_message, connector_id=connector_id))
        self._record("TriggerMessage", dict(response.__dict__), direction="in")
        return response

    async def change_availability(self, connector_id: int, availability_type: str) -> call_result.ChangeAvailabilityPayload:
        payload = {"connector_id": connector_id, "type": availability_type}
        self._record("ChangeAvailability", payload, direction="out")
        response = await self.call(call.ChangeAvailabilityPayload(connector_id=connector_id, type=availability_type))
        self._record("ChangeAvailability", dict(response.__dict__), direction="in")
        return response

    async def unlock_connector(self, connector_id: int) -> call_result.UnlockConnectorPayload:
        payload = {"connector_id": connector_id}
        self._record("UnlockConnector", payload, direction="out")
        response = await self.call(call.UnlockConnectorPayload(connector_id=connector_id))
        self._record("UnlockConnector", dict(response.__dict__), direction="in")
        return response

    async def clear_cache(self) -> call_result.ClearCachePayload:
        self._record("ClearCache", {}, direction="out")
        response = await self.call(call.ClearCachePayload())
        self._record("ClearCache", dict(response.__dict__), direction="in")
        return response

    async def get_diagnostics(
        self, location: str, *, retries: int | None = None,
        retry_interval: int | None = None, start_time: str | None = None,
        stop_time: str | None = None,
    ) -> call_result.GetDiagnosticsPayload:
        # Upload URLs may contain credentials or signed tokens. Never persist them.
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(location)
        safe_location = urlunsplit((parts.scheme, parts.hostname or "", "/[redacted]", "", ""))
        payload: dict[str, Any] = {"location": safe_location}
        if retries is not None: payload["retries"] = retries
        if retry_interval is not None: payload["retry_interval"] = retry_interval
        if start_time is not None: payload["start_time"] = start_time
        if stop_time is not None: payload["stop_time"] = stop_time
        self._record("GetDiagnostics", payload, direction="out")
        response = await self.call(call.GetDiagnosticsPayload(
            location=location, retries=retries, retry_interval=retry_interval,
            start_time=start_time, stop_time=stop_time,
        ))
        self._record("GetDiagnostics", dict(response.__dict__), direction="in")
        return response

    async def update_firmware(
        self, location: str, retrieve_date: str, *,
        retries: int | None = None, retry_interval: int | None = None,
    ) -> call_result.UpdateFirmwarePayload:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(location)
        safe_url = urlunsplit((parts.scheme, parts.hostname or "", "/[redacted]", "", ""))
        recorded = {"location": safe_url, "retrieve_date": retrieve_date}
        if retries is not None:
            recorded["retries"] = retries
        if retry_interval is not None:
            recorded["retry_interval"] = retry_interval
        self._record("UpdateFirmware", recorded, direction="out")
        response = await self.call(call.UpdateFirmwarePayload(
            location=location, retrieve_date=retrieve_date,
            retries=retries, retry_interval=retry_interval,
        ))
        self._record("UpdateFirmware", dict(response.__dict__), direction="in")
        return response

    async def reserve_now(self, connector_id: int, expiry_date: str, id_tag: str, reservation_id: int, parent_id_tag: str | None = None) -> call_result.ReserveNowPayload:
        payload: dict[str, Any] = {"connector_id": connector_id, "expiry_date": expiry_date, "id_tag": id_tag, "reservation_id": reservation_id}
        if parent_id_tag is not None:
            payload["parent_id_tag"] = parent_id_tag
        self._record("ReserveNow", payload, direction="out")
        response = await self.call(call.ReserveNowPayload(**payload))
        self._record("ReserveNow", dict(response.__dict__), direction="in")
        return response

    async def cancel_reservation(self, reservation_id: int) -> call_result.CancelReservationPayload:
        self._record("CancelReservation", {"reservation_id": reservation_id}, direction="out")
        response = await self.call(call.CancelReservationPayload(reservation_id=reservation_id))
        self._record("CancelReservation", dict(response.__dict__), direction="in")
        return response

    async def data_transfer(self, vendor_id: str, message_id: str | None = None, data: str | None = None) -> call_result.DataTransferPayload:
        payload: dict[str, Any] = {"vendor_id": vendor_id}
        if message_id is not None: payload["message_id"] = message_id
        if data is not None: payload["data"] = data
        self._record("DataTransfer", payload, direction="out")
        response = await self.call(call.DataTransferPayload(vendor_id=vendor_id, message_id=message_id, data=data))
        self._record("DataTransfer", dict(response.__dict__), direction="in")
        return response
