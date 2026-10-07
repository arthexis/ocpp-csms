from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.server import WebSocketServerProtocol

from ocpp_csms.connector_query import physical_connector_ids
from ocpp_csms.control import ControlServer, control_socket_path
from ocpp_csms.events import EventStore
from ocpp_csms.runtime import remove_pid, write_pid
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transaction_query import TransactionQuery
from ocpp_csms.transactions import TransactionArchive

LOGGER = logging.getLogger(__name__)
OCPP_16_SUBPROTOCOL = "ocpp1.6"


def charge_point_id_from_path(path: str) -> str:
    clean_path = path.split("?", 1)[0].rstrip("/")
    return clean_path.rsplit("/", 1)[-1] if clean_path else ""


class RecordedWebSocket:
    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket
        self.last_frame: Any = None

    async def recv(self) -> Any:
        self.last_frame = await self.websocket.recv()
        return self.last_frame

    def __getattr__(self, name: str) -> Any:
        return getattr(self.websocket, name)


@dataclass(frozen=True)
class CSMSServer:
    host: str
    port: int
    transactions: TransactionArchive
    events: EventStore
    _active_sessions: dict[str, ChargePointSession] = field(default_factory=dict, init=False, repr=False)

    def session(self, charge_point_id: str) -> ChargePointSession | None:
        return self._active_sessions.get(charge_point_id)

    def connected_chargers(self) -> list[str]:
        return sorted(self._active_sessions)

    def physical_connector_ids(self, charge_point_id: str) -> list[int]:
        return physical_connector_ids(self.events.data_dir, charge_point_id)

    def active_transactions(self, charge_point_id: str) -> list[tuple[int, int | None]]:
        return [
            (view.transaction_id, view.connector_id)
            for view in TransactionQuery(self.transactions.data_dir).active(charger=charge_point_id)
        ]

    def active_transaction_ids(self, charge_point_id: str) -> list[int]:
        return [transaction_id for transaction_id, _connector_id in self.active_transactions(charge_point_id)]

    def record_control_event(
        self,
        event: str,
        *,
        charger_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._record_runtime(event, charger_id=charger_id, details=details)

    def latest_rfid_list_version(self, charger_id: str) -> int | None:
        return self.events.latest_rfid_list_version(charger_id)

    def record_rfid_list(
        self,
        charger_id: str,
        *,
        list_version: int,
        entries: list[dict[str, Any]],
        source_file: str | None,
        list_hash: str,
        verified_version: int | None,
    ) -> int:
        return self.events.record_rfid_list(
            charger_id,
            list_version=list_version,
            entries=entries,
            source_file=source_file,
            list_hash=list_hash,
            verified_version=verified_version,
        )

    def _record_runtime(
        self,
        event: str,
        *,
        charger_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.events.record_runtime(event, charger_id=charger_id, details=details)
        except Exception:
            LOGGER.exception("Could not persist runtime event %s", event)

    async def serve_forever(self) -> None:
        write_pid(self.events.data_dir)
        self._record_runtime("server_started")
        socket_path = control_socket_path(self.events.data_dir)
        try:
            async with ControlServer(self, socket_path):
                async with websockets.serve(
                    self.accept,
                    self.host,
                    self.port,
                    subprotocols=[OCPP_16_SUBPROTOCOL],
                ):
                    LOGGER.info("OCPP CSMS listening on %s:%s", self.host, self.port)
                    LOGGER.info("OCPP CSMS control socket listening at %s", socket_path)
                    await asyncio.Future()
        finally:
            self._record_runtime("server_stopped")
            remove_pid(self.events.data_dir)

    async def accept(self, websocket: WebSocketServerProtocol) -> None:
        path = websocket.path
        charge_point_id = charge_point_id_from_path(path)
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
        self._active_sessions[charge_point_id] = session
        LOGGER.info("Charge point connected: %s", charge_point_id)
        self._record_runtime(
            "charger_connected",
            charger_id=charge_point_id,
            details={
                "path": path,
                "charge_point_id": charge_point_id,
                "subprotocol": websocket.subprotocol,
            },
        )
        try:
            await session.start()
        finally:
            if self._active_sessions.get(charge_point_id) is session:
                del self._active_sessions[charge_point_id]
                LOGGER.info("Charge point disconnected: %s", charge_point_id)
                self._record_runtime("charger_disconnected", charger_id=charge_point_id)
            else:
                LOGGER.info("Superseded charge point connection closed: %s", charge_point_id)
