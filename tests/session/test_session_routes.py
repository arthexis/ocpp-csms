"""Guard actual ChargePointSession route registration after handler extraction."""

from types import SimpleNamespace

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions.archive import TransactionArchive


def test_concrete_session_registers_all_inherited_ocpp_routes(tmp_path):
    session = ChargePointSession(
        "charger-a",
        SimpleNamespace(last_frame=None),
        TransactionArchive(tmp_path),
        EventStore(tmp_path),
    )
    assert {
        "BootNotification",
        "Heartbeat",
        "Authorize",
        "StatusNotification",
        "StartTransaction",
        "StopTransaction",
        "MeterValues",
    } <= set(session.route_map)
