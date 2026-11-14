"""Offline Stage-2 dispatcher family parity and invalid-request safety tests."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ocpp_csms.control import dispatch_control
from ocpp_csms.control.dispatch import RFID_CONFIGURATION_KEYS


class RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.fail_on: str | None = None

    def __getattr__(self, name: str):
        async def invoke(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == self.fail_on:
                raise RuntimeError("injected session failure")
            if name == "get_local_list_version":
                return SimpleNamespace(list_version=2, status="Accepted")
            if name == "get_configuration":
                return SimpleNamespace(configuration_key=[], status="Accepted")
            return SimpleNamespace(status="Accepted")

        return invoke


class RecordingRegistry:
    def __init__(self, session: RecordingSession, *, connected: bool = True) -> None:
        self.recording_session = session
        self.connected = connected
        self.calls: list[str] = []

    def connected_chargers(self):
        self.calls.append("connected_chargers")
        return ["CP-1"] if self.connected else []

    def session(self, charger):
        self.calls.append("session")
        return self.recording_session if charger == "CP-1" and self.connected else None

    async def tls_control(self, command):
        self.calls.append("tls_control")
        return {"ok": True, "response": {"listener": "stopped", "command": command}}

    async def reset_cp_data(self, charger):
        self.calls.append("reset_cp_data")
        return {"charger": charger}

    def physical_connector_ids(self, charger):
        return [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize(("request", "error"), [
    ({"command": "data_transfer", "vendor_id": ""}, "invalid_vendor_id"),
    ({"command": "reserve", "connector": False}, "invalid_connector"),
    ({"command": "cancel_reservation", "reservation_id": 0}, "invalid_reservation_id"),
    ({"command": "update_firmware", "location": ""}, "invalid_location"),
    ({"command": "get_diagnostics", "location": ""}, "invalid_location"),
    ({"command": "trigger", "message": "Wrong"}, "invalid_trigger_message"),
    ({"command": "availability", "connector": False}, "invalid_connector"),
    ({"command": "unlock", "connector": 0}, "invalid_connector"),
    ({"command": "start", "id_tag": ""}, "missing_id_tag"),
    ({"command": "stop", "transaction": -1}, "invalid_transaction"),
    ({"command": "reset", "type": "Invalid"}, "invalid_reset_type"),
    ({"command": "config", "keys": [False]}, "invalid_keys"),
    ({"command": "config_set", "key": ""}, "invalid_key"),
    ({"command": "rfid_export", "entries": {}}, "invalid_rfid_entries"),
    ({"command": "set_charging_profile", "connector": -1}, "invalid_connector"),
    ({"command": "clear_charging_profile", "id": -1}, "invalid_profile_id"),
    ({"command": "get_composite_schedule", "connector": -1}, "invalid_connector"),
])
async def test_invalid_family_requests_do_not_send_ocpp(request, error):
    session = RecordingSession()
    registry = RecordingRegistry(session)
    assert await dispatch_control(registry, request) == {"error": error}
    assert session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("request", "method"), [
    ({"command": "data_transfer", "vendor_id": "vendor"}, "data_transfer"),
    ({"command": "availability", "connector": 0, "type": "Operative"}, "change_availability"),
    ({"command": "rfid_version"}, "get_local_list_version"),
    ({"command": "set_charging_profile", "connector": 0, "profile": {}, "force": True}, "set_charging_profile"),
])
async def test_family_success_preserves_response_envelope(request, method):
    session = RecordingSession()
    result = await dispatch_control(RecordingRegistry(session), request)
    assert result["ok"] is True
    assert result["response"]["status"] == "Accepted"
    assert any(name == method for name, _, _ in session.calls)
    if method == "get_local_list_version":
        assert result["response"]["charger"] == "CP-1"
        assert any(name == "get_configuration" and args[0] == RFID_CONFIGURATION_KEYS
                   for name, args, _ in session.calls)


@pytest.mark.asyncio
async def test_dispatch_facade_retains_preconditions_and_exception_boundary():
    session = RecordingSession()
    assert await dispatch_control(RecordingRegistry(session), {}) == {"error": "missing_command"}
    assert await dispatch_control(RecordingRegistry(session, connected=False), {"command": "unknown"}) == {"error": "no_charger_connected"}
    assert await dispatch_control(RecordingRegistry(session), {"command": "unknown"}) == {"error": "unknown_command", "command": "unknown"}
    assert session.calls == []

    registry = RecordingRegistry(session)
    assert await dispatch_control(registry, {"command": "tls_status"}) == {"ok": True, "response": {"listener": "stopped", "command": "status"}}
    assert "session" not in registry.calls
    assert await dispatch_control(registry, {"command": "data_reset_cp", "charger": "bad/name"}) == {"error": "invalid_charger"}
    assert session.calls == []

    session.fail_on = "reset"
    assert await dispatch_control(registry, {"command": "reset"}) == {"error": "command_failed", "detail": "injected session failure"}
