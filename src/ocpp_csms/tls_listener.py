"""Runtime TLS listener management without changing the plaintext WS listener."""
from __future__ import annotations

import asyncio
import logging
import ssl
from dataclasses import replace
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


class TLSListener:
    """Retain a stable TLS socket and reload its SSL context for new handshakes."""

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

    def status(self) -> dict[str, object]:
        try:
            configured = tls_config.status(self.config_path)
        except ValueError as exc:
            configured = {"configured": False, "error": str(exc)}
        return {
            **configured,
            "listener": "listening" if self.server is not None else "stopped",
            "active_port": self.active.port if self.active else None,
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

    async def _apply(self, config: tls_config.TLSConfig) -> None:
        fresh_context = load_tls_context(config, ws_port=self._ws_port)
        if self.server is not None:
            if self.active is None or config.port != self.active.port:
                raise ValueError("tls_port_change_requires_drain_2d")
            # Existing handshakes use their established SSL objects. Updating
            # the server SSLContext in place changes credentials for new ones.
            assert self.context is not None
            self.context.load_cert_chain(config.cert, config.key)
        else:
            if self._handler is None:
                raise RuntimeError("tls_server_not_running")
            self.server = await websockets.serve(
                self._handler, self._host, config.port,
                ssl=fresh_context, subprotocols=[self._subprotocol],
            )
            self.context = fresh_context
        self.active = config
        self.error = None

    async def command(self, action: str) -> dict[str, object]:
        if action == "status":
            return {"ok": True, "response": self.status()}
        async with self._lock:
            previous = tls_config.read_config(self.config_path)
            try:
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
                    # Graceful listener retirement belongs to 2D. Refuse to
                    # close an active listener, rather than cut charger sessions.
                    if self.server is not None:
                        raise ValueError("tls_disable_requires_drain_2d")
                    if previous is not None:
                        tls_config.write_config(replace(previous, enabled=False), self.config_path)
                else:
                    raise ValueError("unknown_tls_action")
                return {"ok": True, "response": self.status()}
            except (ValueError, OSError, ssl.SSLError) as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                return {"error": "tls_operation_failed", "detail": self.error}

    async def stop(self) -> None:
        async with self._lock:
            if self.server is not None:
                self.server.close()
                try:
                    await self.server.wait_closed()
                finally:
                    self.server = None
                    self.context = None
                    self.active = None
