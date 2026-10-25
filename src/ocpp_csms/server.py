from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.server import WebSocketServerProtocol

from ocpp_csms.connector_query import physical_connector_ids
from ocpp_csms.evidence.store import DATABASE_FILENAME
from ocpp_csms.control import ControlServer, control_socket_path
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.persistent_wal import persistent_wal
from ocpp_csms.runtime import remove_pid, write_pid
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions.query import TransactionQuery
from ocpp_csms.transactions.archive import TransactionArchive
from ocpp_csms.tls_listener import TLSListener
from ocpp_csms.auth import load_auth, basic_upgrade_check, auth_path

LOGGER = logging.getLogger(__name__)
OCPP_16_SUBPROTOCOL = "ocpp1.6"


def charge_point_id_from_path(path: str) -> str:
    clean_path = path.split("?", 1)[0].rstrip("/")
    return clean_path.rsplit("/", 1)[-1] if clean_path else ""


class AuthenticatedWebSocketProtocol(WebSocketServerProtocol):
    """Recognize local TLS termination from the TCP peer, not HTTP headers."""

    def __init__(self, *args, auth: dict, **kwargs):
        super().__init__(*args, **kwargs)
        self.auth = auth

    async def process_request(self, path, headers):
        peer = self.remote_address
        local_proxy = False
        if peer:
            try:
                local_proxy = ipaddress.ip_address(peer[0]).is_loopback
            except ValueError:
                pass
        return basic_upgrade_check(
            self.auth, path, headers, secure=local_proxy,
            config_path=auth_path() if local_proxy else None,
        )


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
    _tls_listener: TLSListener | None = field(default=None, init=False, repr=False)
    _resetting_charge_points: set[str] = field(default_factory=set, init=False, repr=False)

    async def reset_cp_data(self, cp: str) -> dict:
        if cp in self._resetting_charge_points:
            raise ValueError("charge point reset already running")
        if cp in self._active_sessions:
            raise ValueError("charge point connected; disconnect before reset")
        from ocpp_csms.cli.data import reset_live_cp
        self._resetting_charge_points.add(cp)
        try:
            return await asyncio.to_thread(reset_live_cp, self.events.data_dir, cp)
        finally:
            self._resetting_charge_points.discard(cp)

    async def tls_control(self, action: str) -> dict[str, object]:
        if self._tls_listener is None:
            return {"error": "tls_server_not_running"}
        return await self._tls_listener.command(action)

    def session(self, charge_point_id: str) -> ChargePointSession | None:
        return self._active_sessions.get(charge_point_id)

    def connected_chargers(self) -> list[str]:
        return sorted(self._active_sessions)

    def physical_connector_ids(self, charge_point_id: str) -> list[int]:
        return physical_connector_ids(self.events.data_dir, charge_point_id)

    def active_transactions(self, charge_point_id: str) -> list[tuple[int, int | None]]:
        # Historical open transactions, not live connector occupancy.
        return [
            (view.transaction_id, view.connector_id)
            for view in TransactionQuery(self.transactions.data_dir).active(charger=charge_point_id)
        ]

    def active_transaction_ids(self, charge_point_id: str) -> list[int]:
        return [transaction_id for transaction_id, _connector_id in self.active_transactions(charge_point_id)]

    def connector_occupancy(self, charge_point_id: str) -> dict[int, str]:
        """Latest connector reports from the current WebSocket connection only."""
        session = self.session(charge_point_id)
        return dict(session.connector_statuses) if session is not None else {}

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
        # Evidence is stored in SQLite; do not duplicate every OCPP frame in journald.
        logging.getLogger("ocpp").setLevel(logging.WARNING)
        logging.getLogger("websockets").setLevel(logging.WARNING)
        write_pid(self.events.data_dir)
        self._record_runtime("server_started")
        # Invalid auth configuration must fail startup, not open an anonymous listener.
        auth = load_auth()
        socket_path = control_socket_path(self.events.data_dir)
        try:
            with persistent_wal(self.events.data_dir):
                await self._serve_listeners(socket_path, auth)
        finally:
            self._record_runtime("server_stopped")
            remove_pid(self.events.data_dir)

    async def _serve_listeners(self, socket_path, auth) -> None:
        async with ControlServer(self, socket_path):
                async with websockets.serve(
                    self.accept,
                    self.host,
                    self.port,
                    subprotocols=[OCPP_16_SUBPROTOCOL],
                    create_protocol=lambda *args, **kwargs: AuthenticatedWebSocketProtocol(
                        *args, auth=auth, **kwargs
                    ),
                ):
                    # TLS startup is independent of the already-bound WS listener.
                    # A bad certificate or occupied WSS port must not stop WS.
                    tls = TLSListener()
                    object.__setattr__(self, "_tls_listener", tls)
                    try:
                        await tls.start(
                            self.accept,
                            host=self.host,
                            ws_port=self.port,
                            subprotocol=OCPP_16_SUBPROTOCOL,
                            process_request=lambda path, headers: basic_upgrade_check(auth, path, headers, secure=True, config_path=auth_path()),
                        )
                        LOGGER.info("OCPP CSMS listening on %s:%s", self.host, self.port)
                        LOGGER.info("OCPP CSMS control socket listening at %s", socket_path)
                        await asyncio.Future()
                    finally:
                        object.__setattr__(self, "_tls_listener", None)
                        await tls.stop()
        finally:
            self._record_runtime("server_stopped")
            remove_pid(self.events.data_dir)

    async def accept(self, websocket: WebSocketServerProtocol) -> None:
        path = websocket.path
        charge_point_id = charge_point_id_from_path(path)
        if charge_point_id in self._resetting_charge_points:
            await websocket.close(code=1013, reason="Charge point maintenance")
            return
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
        previous = self._active_sessions.get(charge_point_id)
        self._active_sessions[charge_point_id] = session
        if previous is not None:
            await previous.connection.close(code=1001, reason="Superseded connection")
        LOGGER.info("Charge point connected: %s", charge_point_id)
        # Persist only allowlisted, non-credential handshake diagnostics.
        # Do not persist the entire request headers (Authorization/cookies).
        headers = getattr(websocket, "request_headers", None)
        remote = getattr(websocket, "remote_address", None)
        details = {
            "path": path.split("?", 1)[0],
            "charge_point_id": charge_point_id,
            "subprotocol": websocket.subprotocol,
        }
        if isinstance(remote, tuple) and len(remote) >= 2:
            details["remote"] = f"{remote[0]}:{remote[1]}"
        if headers is not None:
            user_agent = headers.get("User-Agent")
            requested = headers.get("Sec-WebSocket-Protocol")
            if user_agent:
                details["user_agent"] = user_agent[:256]
            if requested:
                details["requested_subprotocols"] = requested[:256]
        self._record_runtime(
            "charger_connected",
            charger_id=charge_point_id,
            details=details,
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
