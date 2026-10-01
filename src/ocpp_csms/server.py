from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import websockets
from websockets.server import WebSocketServerProtocol

from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions import TransactionArchive

LOGGER = logging.getLogger(__name__)
OCPP_16_SUBPROTOCOL = "ocpp1.6"


@dataclass(frozen=True)
class CSMSServer:
    host: str
    port: int
    transactions: TransactionArchive

    async def serve_forever(self) -> None:
        async with websockets.serve(
            self.accept,
            self.host,
            self.port,
            subprotocols=[OCPP_16_SUBPROTOCOL],
        ):
            LOGGER.info("OCPP CSMS listening on %s:%s", self.host, self.port)
            await asyncio.Future()

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

        session = ChargePointSession(charge_point_id, websocket, self.transactions)
        LOGGER.info("Charge point connected: %s", charge_point_id)
        try:
            await session.start()
        finally:
            LOGGER.info("Charge point disconnected: %s", charge_point_id)
