from __future__ import annotations

import asyncio
import json
import os
import socket
import struct
from pathlib import Path
from typing import Any

from .dispatch import dispatch_control
from .protocols import SessionRegistry

CONTROL_SOCKET_FILENAME = "control.sock"


def control_socket_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / CONTROL_SOCKET_FILENAME



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
                    command = request.get("command")
                    if command in {"tls_status", "tls_enable", "tls_reload", "tls_disable"}:
                        sock = writer.get_extra_info("socket")
                        try:
                            raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                            _pid, uid, _gid = struct.unpack("3i", raw)
                            authorized = uid in (0, os.geteuid())
                        except (AttributeError, OSError, struct.error):
                            authorized = False
                        response = await dispatch_control(self.registry, request) if authorized else {"error": "tls_permission_denied"}
                    else:
                        response = await dispatch_control(self.registry, request)
            writer.write(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

