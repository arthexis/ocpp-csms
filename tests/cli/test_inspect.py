"""Inspect command is investigative, read-only and resilient to failed live queries."""
from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ocpp_csms.cli.inspect import _inspect, _safe_query, _targets


def test_targets_default_single_connected():
    snapshot = {"chargers": [SimpleNamespace(charger_id="CP1", connected=True)]}
    assert _targets(snapshot, charger=None, all_chargers=False) == ["CP1"]


def test_targets_requires_selection_for_multiple():
    snapshot = {"chargers": [SimpleNamespace(charger_id="CP1", connected=True),
                              SimpleNamespace(charger_id="CP2", connected=True)]}
    with pytest.raises(ValueError, match="specify"):
        _targets(snapshot, charger=None, all_chargers=False)


@pytest.mark.asyncio
async def test_offline_never_queries_charger(monkeypatch):
    import ocpp_csms.cli.inspect as module
    monkeypatch.setattr(module, "_reconciliation", lambda *a: [])
    sentinel = AsyncMock(side_effect=AssertionError("offline queried charger"))
    monkeypatch.setattr(module, "_safe_query", sentinel)
    snapshot = {"chargers": [SimpleNamespace(charger_id="CP1", connected=True)]}
    report = await _inspect("/tmp/unused", "CP1", snapshot, offline=True, deep=False, timeout=1)
    assert report["queries"] == {}
    sentinel.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_errors_are_isolated(monkeypatch):
    import ocpp_csms.cli.inspect as module
    monkeypatch.setattr(module, "send_control", AsyncMock(return_value={"error": "command_failed"}))
    result = await _safe_query("/tmp/unused", "CP1", "config", 1)
    assert result == {"state": "unknown", "reason": "command_failed"}


@pytest.mark.asyncio
async def test_default_only_queries_configuration(monkeypatch):
    import ocpp_csms.cli.inspect as module
    monkeypatch.setattr(module, "_reconciliation", lambda *a: [])
    send = AsyncMock(return_value={"state": "observed", "response": {"configuration_key": []}})
    monkeypatch.setattr(module, "_safe_query", send)
    snapshot = {"chargers": [SimpleNamespace(charger_id="CP1", connected=True)]}
    report = await _inspect("/tmp/unused", "CP1", snapshot, offline=False, deep=False, timeout=1)
    assert list(report["queries"]) == ["configuration"]
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_deep_runs_independent_checks_on_failures(monkeypatch):
    import ocpp_csms.cli.inspect as module
    monkeypatch.setattr(module, "_reconciliation", lambda *a: [])
    send = AsyncMock(side_effect=[
        {"state": "unknown", "reason": "timeout"},
        {"state": "observed", "response": {"list_version": 4}},
        {"state": "unknown", "reason": "NotSupported"},
    ])
    monkeypatch.setattr(module, "_safe_query", send)
    snapshot = {"chargers": [SimpleNamespace(charger_id="CP1", connected=True)]}
    report = await _inspect("/tmp/unused", "CP1", snapshot, offline=False, deep=True, timeout=1)
    assert len(report["queries"]) == 3
    assert send.await_count == 3
    assert len([finding for finding in report["findings"] if finding["severity"] == "unknown"]) == 2
