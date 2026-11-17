"""Stage 4D: exact event summary presentation with preserved dynamic facades."""
from __future__ import annotations

import json

import pytest

import ocpp_csms.evidence.diagnostics as diagnostics
from ocpp_csms.evidence import _diagnostics_presentation as presentation


def event(action, payload, *, kind="ocpp", direction="in", id_tag=None, transaction_id=None):
    return {
        "kind": kind,
        "action": action,
        "direction": direction,
        "payload": json.dumps(payload) if isinstance(payload, (dict, list)) else payload,
        "id_tag": id_tag,
        "transaction_id": transaction_id,
        "charger_id": "CP-A",
        "occurred_at": "2026-11-16T10:00:00Z",
    }


@pytest.mark.parametrize(("row", "expected"), [
    (event("Authorize", "{"), "Authorize [invalid payload]"),
    (event("Authorize", [1]), "Authorize [unexpected payload]"),
    (event("charger_connected", {"path": "/ws"}, kind="runtime"), "charger connected"),
    (event("BootNotification", {"charge_point_vendor": "Acme"}), "BootNotification"),
    (event("BootNotification", {"status": "Accepted", "interval": 30}, direction="out"),
     "→ BootNotification status=Accepted interval=30"),
    (event("StatusNotification", {"connector_id": 1, "status": "Charging",
                                 "error_code": "NoError", "info": "ok"}),
     "StatusNotification C1 Charging ok"),
    (event("StartTransaction", {"meter_start": 42}, id_tag="CARD", transaction_id=7),
     "StartTransaction RFID CARD tx=7 meter_start=42"),
    (event("Heartbeat", {"unexpected": "value"}, direction="out"),
     "→ Heartbeat response extra=1"),
])
def test_existing_compact_summary_and_new_pure_renderer_are_identical(row, expected):
    assert diagnostics._summary(row) == expected
    assert presentation.render_summary(row) == expected


def test_connection_metadata_detail_preserves_whitelist_and_no_secrets():
    row = event("charger_connected", {
        "path": "/ocpp/CP-A", "subprotocol": "ocpp1.6",
        "authorization": "sensitive-token",
    }, kind="runtime")
    expected = 'charger connected path="/ocpp/CP-A" subprotocol="ocpp1.6"'
    assert diagnostics._summary(row, detail=True) == expected
    assert presentation.render_summary(row, detail=True) == expected
    assert "sensitive-token" not in diagnostics.format_events([row], detail=True)


def test_boot_identity_and_response_extras_preserve_detail():
    boot = event("BootNotification", {
        "charge_point_vendor": "Acme", "charge_point_model": "Dual",
        "firmware_version": "1.2.3",
    })
    assert diagnostics._summary(boot, detail=True) == (
        "BootNotification charge_point_vendor=Acme charge_point_model=Dual firmware_version=1.2.3"
    )
    response = event("Authorize", {
        "idTagInfo": {"status": "Accepted"}, "unfamiliar": {"a": 1},
    }, direction="out")
    assert diagnostics._summary(response) == "→ Authorize Accepted extra=1"
    assert diagnostics._summary(response, detail=True) == (
        '→ Authorize Accepted extra={"unfamiliar": {"a": 1}}'
    )


def test_public_event_formatter_still_uses_dynamic_local_summary(monkeypatch):
    row = event("BootNotification", {"charge_point_vendor": "Acme"})
    calls = []

    def substitute(record, *, detail=False):
        calls.append((record, detail))
        return "patched legacy summary"

    monkeypatch.setattr(diagnostics, "_summary", substitute)
    assert diagnostics.format_events([row], detail=True) == (
        "2026-11-16T10:00:00Z CP-A  patched legacy summary"
    )
    assert calls == [(row, True)]


def test_legacy_facade_resolves_private_renderer_at_call_time(monkeypatch):
    row = event("BootNotification", {})
    calls = []

    def substitute(record, *, detail=False):
        calls.append((record, detail))
        return "patched private renderer"

    monkeypatch.setattr(presentation, "render_summary", substitute)
    assert diagnostics._summary(row, detail=True) == "patched private renderer"
    assert calls == [(row, True)]


def test_rendering_does_not_touch_sqlite(monkeypatch):
    monkeypatch.setattr(
        diagnostics.sqlite3, "connect",
        lambda *args, **kwargs: pytest.fail("presentation opened SQLite"),
    )
    row = event("BootNotification", {"charge_point_vendor": "Acme"})
    assert diagnostics._summary(row) == "BootNotification"
    assert "BootNotification" in diagnostics.format_events([row])


def test_missing_required_row_key_error_is_not_suppressed():
    row = event("BootNotification", {})
    del row["payload"]
    with pytest.raises(KeyError, match="payload"):
        diagnostics._summary(row)
