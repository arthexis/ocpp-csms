"""Control socket permission checks for privileged TLS operations."""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from ocpp_csms.control import ControlServer, send_control


class Registry:
    def __init__(self):
        self.actions = []
    async def tls_control(self, action):
        self.actions.append(action)
        return {"ok": True, "response": {"action": action}}


@pytest.mark.asyncio
async def test_tls_commands_use_peer_credentials_without_charger(tmp_path):
    registry = Registry()
    path = tmp_path / "control.sock"
    async with ControlServer(registry, path):
        assert path.stat().st_mode & 0o777 == 0o660
        result = await send_control(tmp_path, {"command": "tls_status"})
        assert result == {"ok": True, "response": {"action": "status"}}
        assert registry.actions == ["status"]


@pytest.mark.asyncio
async def test_tls_commands_reject_non_root_non_service_peer(tmp_path, monkeypatch):
    if os.getuid() == 0:
        pytest.skip("root is always an authorized TLS operator")
    registry = Registry()
    path = tmp_path / "control.sock"
    # Simulate a process identity that differs from the actual connected UID.
    monkeypatch.setattr("ocpp_csms.control.os.geteuid", lambda: os.getuid() + 20000)
    async with ControlServer(registry, path):
        result = await send_control(tmp_path, {"command": "tls_enable"})
        assert result == {"error": "tls_permission_denied"}
        assert registry.actions == []
