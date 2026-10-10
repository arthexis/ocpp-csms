from types import SimpleNamespace

import pytest

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions import TransactionArchive


@pytest.fixture
def charge_point_session(tmp_path):
    return ChargePointSession(
        "charger-a",
        SimpleNamespace(last_frame="test"),
        TransactionArchive(tmp_path),
        EventStore(tmp_path),
    )
