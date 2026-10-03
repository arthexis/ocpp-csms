from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Protocol


CONTROL_SOCKET_FILENAME = "control.sock"


class Session(Protocol):
    async def remote_start(self, *, id_tag: str, connector_id: int | None = None) -> Any: ...

    async def remote_stop(self, transaction_id: int) -> Any: ...

    async def reset(self, reset_type: str = "Soft") -> Any: ...

    async def get_configuration(self, keys: list[str] | None = None) -> Any: ...


class SessionRegistry(Protocol):
    def session(self, charge_point_id: str) -> Session | None: ...

    def active_transaction_ids(self, charge_point_id: str) -> list[int]: ...


def control_socket_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / CONTROL_SOCKET_FILENAME


def _response_payload(response: Any) -> dict[str, Any]:
    payload = getattr(response, "__dict__", None)
    if isinstance(payload, dict):
        return dict(payload)
    return {"status": getattr(response, "status", None)}


async def send_control(data_dir: str | Path, request: dict[str, Any]) -> dict[str, Any]:
    path = control_socket_path(data_dir)
    reader, writer = await asyncio.open_unix_connection(str(path))
    try:
        writer.write(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
        await writer.drain()
        line = await reader.readline()
        if not line:
            raise ConnectionError("control socket closed without a response")
        response = json.loads(line)
        if not isinstance(response, dict):
            raise ValueError("invalid control response")
        return response
    finally:
        writer.close()
        await writer.wait_closed()


def _configuration_keys(request: dict[str, Any]) -> list[str] | None | object:
    keys = request.get("keys")
    if keys is None or keys == []:
        return None
    if not isinstance(keys, list):
        return _INVALID
    if any(not isinstance(key, str) or not key for key in keys):
        return _INVALID
    return keys


_INVALID = object()


async def dispatch_control(registry: SessionRegistry, request: dict[str, Any]) -> dict[str, Any]:
    command = request.get("command")
    charger = request.get("charger")
    if not isinstance(command, str) or not command:
        return {"error": "missing_command"}
    if not isinstance(charger, str) or not charger:
        return {"error": "missing_charger"}

    session = registry.session(charger)
    if session is None:
        return {"error": "charger_not_connected", "charger": charger}

    try:
        if command == "start":
            id_tag = request.get("id_tag")
            connector = request.get("connector")
            if not isinstance(id_tag, str) or not id_tag:
                return {"error": "missing_id_tag"}
            if connector is not None and (not isinstance(connector, int) or isinstance(connector, bool) or connector < 0):
                return {"error": "invalid_connector"}
            response = await session.remote_start(id_tag=id_tag, connector_id=connector)
        elif command == "stop":
            transaction = request.get("transaction")
            if not isinstance(transaction, int) or isinstance(transaction, bool) or transaction < 0:
                return {"error": "invalid_transaction"}
            response = await session.remote_stop(transaction)
        elif command == "reboot":
            reset_type = request.get("type", "Soft")
            if reset_type not in {"Soft", "Hard"}:
                return {"error": "invalid_reset_type"}
            response = await session.reset(reset_type)
        elif command == "config":
            keys = _configuration_keys(request)
            if keys is _INVALID:
                return {"error": "invalid_keys"}
            force = request.get("force", False)
            if not isinstance(force, bool):
                return {"error": "invalid_force"}
            active_transactions = registry.active_transaction_ids(charger)
            if active_transactions and not force:
                return {
                    "error": "active_transaction",
                    "charger": charger,
                    "transactions": active_transactions,
                }
            response = await session.get_configuration(keys)
        else:
            return {"error": "unknown_command", "command": command}
    except Exception as exc:
        return {"error": "command_failed", "detail": str(exc)}

    return {"ok": True, "response": _response_payload(response)}


class ControlServer:
    def __init__(self, registry: SessionRegistry, path: str | Path) -> None:
        self.registry = registry
        self.path = Path(path)

    async def __aenter__(self) -> ControlServer:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self.server = await asyncio.start_unix_server(self._handle, path=str(self.path))
        os.chmod(self.path, 0o660)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.server.close()
        await self.server.wait_closed()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            try:
                request = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                response = {"error": "invalid_json"}
            else:
                if not isinstance(request, dict):
                    response = {"error": "invalid_request"}
                else:
                    response = await dispatch_control(self.registry, request)
            writer.write(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
