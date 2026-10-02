from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import websockets
from websockets.server import WebSocketServerProtocol

from ocpp_csms.events import EventStore
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions import TransactionArchive

LOGGER = logging.getLogger(__name__)
OCPP_16_SUBPROTOCOL = "ocpp1.6"


class RecordedWebSocket:
    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket
        self.last_frame: Any = None

    async def recv(self) -> Any:
        self.last_frame = await self.websocket.recv()
        return self.last_frame

    async def send(self, frame: Any) -> None:
        await self.websocket.send(frame)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.websocket, name)


@dataclass(frozen=True)
class CSMSServer:
    host: str
    port: int
    transactions: TransactionArchive
    events: EventStore

    def _record_runtime(self, event: str, *, charger_id: str | None = None) -> None:
        try:
            self.events.record_runtime(event, charger_id=charger_id)
        except Exception:
            LOGGER.exception("Could not persist runtime event %s", event)

    async def serve_forever(self) -> None:
        self._record_runtime("server_started")
        try:
            async with websockets.serve(
                self.accept,
                self.host,
                self.port,
                subprotocols=[OCPP_16_SUBPROTOCOL],
            ):
                LOGGER.info("OCPP CSMS listening on %s:%s", self.host, self.port)
                await asyncio.Future()
        finally:
            self._record_runtime("server_stopped")

    async def accept(self, websocket: WebSocketServerProtocol, path: str) -> None:
        charge_point_id = path.strip("/")
        if not charge_point_id:
            await websocket.close(code=1008, reason="Missing charge point id")
            return

        if websocket.subprotocol != OCPP_16_SUBPROTOCOL:
            LOGGER.warning(
                "Charge point %s connected without negotiated %s subprotocol",
                charge_point_id,
                OCPP_16_SUBPROTOCOL,
            )

        connection = RecordedWebSocket(websocket)
        session = ChargePointSession(
            charge_point_id,
            connection,
            self.transactions,
            self.events,
        )
        LOGGER.info("Charge point connected: %s", charge_point_id)
        self._record_runtime("charger_connected", charger_id=charge_point_id)
        try:
            await session.start()
        finally:
            LOGGER.info("Charge point disconnected: %s", charge_point_id)
            self._record_runtime("charger_disconnected", charger_id=charge_point_id)
