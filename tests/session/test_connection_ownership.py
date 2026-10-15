"""Live connection ownership is independent of durable SQLite evidence."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ocpp_csms.server import CSMSServer
from ocpp_csms.session.connection import ChargePointSession


def test_occupancy_is_scoped_to_current_session(tmp_path):
    from ocpp_csms.evidence.store import EventStore
    from ocpp_csms.transactions.archive import TransactionArchive

    events = EventStore(tmp_path)
    server = CSMSServer("127.0.0.1", 9000, TransactionArchive(tmp_path), events)
    first = SimpleNamespace(connector_statuses={1: "Charging"})
    server._active_sessions["CP1"] = first
    assert server.connector_occupancy("CP1") == {1: "Charging"}

    server._active_sessions["CP1"] = SimpleNamespace(connector_statuses={})
    assert server.connector_occupancy("CP1") == {}
    del server._active_sessions["CP1"]
    assert server.connector_occupancy("CP1") == {}


@pytest.mark.asyncio
async def test_status_updates_session_only_after_sqlite_acceptance(tmp_path):
    from ocpp_csms.evidence.store import EventStore
    from ocpp_csms.transactions.archive import TransactionArchive

    events = EventStore(tmp_path)
    session = ChargePointSession("CP1", SimpleNamespace(), TransactionArchive(tmp_path), events)
    payload = {"connector_id": 1, "status": "Charging", "error_code": "NoError"}
    await session.on_status_notification(**payload)
    assert session.connector_statuses == {1: "Charging"}
    session.events.record_connector_status = lambda *_: False
    await session.on_status_notification(connector_id=1, status="Available", error_code="NoError")
    assert session.connector_statuses == {1: "Charging"}
