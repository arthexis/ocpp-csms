"""Optional WSS listener bootstrap; runtime reconfiguration follows in 2C."""
from __future__ import annotations

import logging
import ssl
from pathlib import Path
from typing import Any, Callable

import websockets

from ocpp_csms import tls_config

LOGGER = logging.getLogger(__name__)


def load_tls_context(config: tls_config.TLSConfig, *, ws_port: int) -> ssl.SSLContext:
    """Validate before opening the listener. Fail closed for WSS, not WS."""
    report = tls_config.check_config(config, ws_port=ws_port)
    if not report["ready"]:
        raise ValueError("tls_not_ready: " + ", ".join(report["errors"]))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config.cert, config.key)
    return context


class TLSListener:
    """Own the optional listener independently of the established WS listener."""

    def __init__(self, config_path: str | Path = tls_config.DEFAULT_CONFIG) -> None:
        self.config_path = Path(config_path)
        self.server: Any | None = None
        self.error: str | None = None

    async def start(self, handler: Callable[..., Any], *, host: str, ws_port: int, subprotocol: str) -> bool:
        try:
            config = tls_config.read_config(self.config_path)
            if config is None or not config.enabled:
                return False
            context = load_tls_context(config, ws_port=ws_port)
            self.server = await websockets.serve(
                handler, host, config.port, ssl=context, subprotocols=[subprotocol],
            )
        except (ValueError, OSError, ssl.SSLError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            LOGGER.error("WSS listener unavailable; plaintext WS remains available: %s", self.error)
            return False
        self.error = None
        LOGGER.info("OCPP WSS listener on %s:%s", host, config.port)
        return True

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            try:
                await self.server.wait_closed()
            finally:
                self.server = None
