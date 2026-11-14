"""Stage 4A: observed connector state is independent of historical SQLite transactions."""
from __future__ import annotations

import pytest

import ocpp_csms.status as status


def observed(connector_id: int, observed_status: str | None, transaction_id: int | None = None):
    return status.ConnectorStatus(
        connector_id=connector_id,
        status=observed_status,
        error_code=None,
        transaction_id=transaction_id,
        id_tag=None,
        started_at=None,
    )


@pytest.mark.parametrize(("readings", "expected"), [
    ([], ("unknown", "unknown")),
    ([(0, "Charging", 44)], ("unknown", "unknown")),
    ([(1, "Available", 44)], ("available", "inactive")),
    ([(1, "Charging", None)], ("unavailable", "active")),
    ([(1, "Charging", 12), (2, "Available", None)], ("available", "active")),
    ([(1, "Preparing", 44)], ("unavailable", "unknown")),
    ([(1, "Faulted", 44)], ("unavailable", "inactive")),
    ([(1, None, 44)], ("unknown", "unknown")),
])
def test_operational_state_never_uses_transaction_as_observed_activity(
    monkeypatch, readings, expected
):
    def unexpected_sql(*args, **kwargs):
        pytest.fail("pure observation attempted SQLite connection")
    monkeypatch.setattr(status.sqlite3, "connect", unexpected_sql)
    connectors = [observed(*reading) for reading in readings]
    assert status.operational_state(connectors) == expected


@pytest.mark.parametrize(("reported", "error", "info", "expected"), [
    ("Faulted", "InternalError", "EmergencyStop", "EmergencyStop"),
    ("Faulted", "InternalError", "Other", "Faulted"),
    ("Faulted", "OtherError", "EmergencyStop", "Faulted"),
    ("Available", "InternalError", "EmergencyStop", "Available"),
    (None, None, None, None),
])
def test_emergency_derivation_keeps_exact_legacy_condition(reported, error, info, expected):
    assert status.derive_status(reported, error, info) == expected


def test_public_status_seams_remain_patchable(monkeypatch):
    monkeypatch.setattr(status, "_observed_derive_status", lambda *args: "patched")
    monkeypatch.setattr(status, "_observed_operational_state", lambda connectors: ("patched", "patched"))
    assert status.derive_status("Faulted", "InternalError", "EmergencyStop") == "patched"
    assert status.operational_state([observed(1, "Available")]) == ("patched", "patched")
