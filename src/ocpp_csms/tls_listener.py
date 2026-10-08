"""Manage a WSS listener independently of the CSMS plaintext WebSocket server."""
from __future__ import annotations

import asyncio
import logging
import ssl
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

import websockets
from ocpp_csms import tls_config

LOGGER = logging.getLogger(__name__)


def load_tls_context(config: tls_config.TLSConfig, *, ws_port: int) -> ssl.SSLContext:
    report = tls_config.check_config(config, ws_port=ws_port)
    if not report["ready"]:
        raise ValueError("tls_not_ready: " + ", ".join(report["errors"]))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config.cert, config.key)
    return context


@dataclass
class DrainingListener:
    port: int
    server: Any
    cleanup: asyncio.Task[None]


class TLSListener:
    """Keep existing connections alive as old listening ports are retired."""

    def __init__(self, config_path: str | Path = tls_config.DEFAULT_CONFIG) -> None:
        self.config_path = Path(config_path)
        self.server: Any | None = None
        self.context: ssl.SSLContext | None = None
        self.active: tls_config.TLSConfig | None = None
        self.error: str | None = None
        self._lock = asyncio.Lock()
        self._handler: Callable[..., Any] | None = None
        self._host = ""
        self._ws_port = 0
        self._subprotocol = ""
        self._draining: list[DrainingListener] = []

    def status(self) -> dict[str, object]:
        try:
            configured = tls_config.status(self.config_path)
        except ValueError as exc:
            configured = {"configured": False, "error": str(exc)}
        return {
            **configured,
            "listener": "listening" if self.server is not None else (
                "draining" if self._draining else "stopped"
            ),
            "active_port": self.active.port if self.active else None,
            "draining_ports": [item.port for item in self._draining],
            "error": self.error,
        }

    async def start(self, handler: Callable[..., Any], *, host: str, ws_port: int, subprotocol: str) -> bool:
        self._handler, self._host = handler, host
        self._ws_port, self._subprotocol = ws_port, subprotocol
        async with self._lock:
            try:
                config = tls_config.read_config(self.config_path)
                if config is None or not config.enabled:
                    return False
                await self._apply(config)
                return True
            except (ValueError, OSError, ssl.SSLError) as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                LOGGER.error("WSS listener unavailable; WS remains available: %s", self.error)
                return False

    def _retire(self, server: Any, port: int) -> None:
        # websockets legacy server: stop accepting new connections, but leave
        # established charge-point sessions running until they disconnect.
        server.close(close_connections=False)
        handle = DrainingListener(port, server, asyncio.create_task(self._finish_retiring(server)))
        self._draining.append(handle)

    async def _finish_retiring(self, server: Any) -> None:
        try:
            await server.wait_closed()
        finally:
            self._draining[:] = [handle for handle in self._draining if handle.server is not server]

    async def _apply(self, config: tls_config.TLSConfig) -> None:
        fresh_context = load_tls_context(config, ws_port=self._ws_port)
        if self.server is not None and self.active is not None and config.port == self.active.port:
            assert self.context is not None
            # Load onto the live context only after validating a separate one.
            # Established TLS handshakes are not renegotiated.
            self.context.load_cert_chain(config.cert, config.key)
        else:
            if self._handler is None:
                raise RuntimeError("tls_server_not_running")
            # Make before break: bind new listener before retiring the old.
            new_server = await websockets.serve(
                self._handler, self._host, config.port,
                ssl=fresh_context, subprotocols=[self._subprotocol],
            )
            previous_server, previous = self.server, self.active
            self.server, self.context = new_server, fresh_context
            if previous_server is not None and previous is not None:
                self._retire(previous_server, previous.port)
        self.active = config
        self.error = None

    async def command(self, action: str) -> dict[str, object]:
        if action == "status":
            return {"ok": True, "response": self.status()}
        async with self._lock:
            try:
                previous = tls_config.read_config(self.config_path)
                if action == "enable":
                    if previous is None:
                        raise ValueError("tls_not_configured")
                    wanted = replace(previous, enabled=True)
                    await self._apply(wanted)
                    tls_config.write_config(wanted, self.config_path)
                elif action == "reload":
                    if previous is None or not previous.enabled:
                        raise ValueError("tls_not_enabled")
                    await self._apply(previous)
                elif action == "disable":
                    if previous is not None:
                        tls_config.write_config(replace(previous, enabled=False), self.config_path)
                    if self.server is not None:
                        assert self.active is not None
                        self._retire(self.server, self.active.port)
                        self.server, self.context, self.active = None, None, None
                    self.error = None
                else:
                    raise ValueError("unknown_tls_action")
                return {"ok": True, "response": self.status()}
            except (ValueError, OSError, ssl.SSLError) as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                return {"error": "tls_operation_failed", "detail": self.error}

    async def stop(self) -> None:
        # Full service shutdown is different from an operational disable:
        # it terminates all sessions, including those on retired listeners.
        async with self._lock:
            servers = ([self.server] if self.server is not None else []) + [
                entry.server for entry in self._draining
            ]
            # An already-draining server's close(False) cannot be undone by
            # calling close(True) again. Explicitly close its live protocols.
            connections = [
                protocol for entry in self._draining
                for protocol in list(entry.server.websockets)
            ]
            for server in servers:
                server.close(close_connections=True)
            await asyncio.gather(*(protocol.close(code=1001) for protocol in connections))
            await asyncio.gather(*(server.wait_closed() for server in servers))
            if self._draining:
                await asyncio.gather(*(entry.cleanup for entry in list(self._draining)))
            self._draining.clear()
            self.server, self.context, self.active = None, None, None
