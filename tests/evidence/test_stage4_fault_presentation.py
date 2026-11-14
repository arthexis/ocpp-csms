"""Stage 4A: human fault text is pure; event query and timestamp seams remain unchanged."""
from __future__ import annotations

import pytest

import ocpp_csms.evidence.diagnostics as diagnostics


def fault_record():
    return {
        "received_at": "2026-11-13T10:01:02.123456Z",
        "charger_id": "CP-1",
        "connector_id": 0,
        "status": "Faulted",
        "error_code": "InternalError",
        "charger_at": "2026-11-13T10:00:58Z",
        "info": "EmergencyStop",
        "vendor_id": "vendor-a",
        "vendor_error_code": "E42",
    }


def test_public_fault_formatter_preserves_all_fields_and_raw_timestamps(monkeypatch):
    monkeypatch.setattr(diagnostics.sqlite3, "connect", lambda *a, **kw: pytest.fail("formatter queried SQLite"))
    assert diagnostics.format_faults([]) == "No recorded fault notifications."
    assert diagnostics.format_faults([fault_record()]) == (
        "2026-11-13T10:01:02.123456Z  CP-1  C0  Faulted  InternalError  "
        "charger_at=2026-11-13T10:00:58Z  info=EmergencyStop  "
        "vendor_id=vendor-a  vendor_error_code=E42"
    )


def test_fault_formatter_uses_dynamic_legacy_timestamp_helper(monkeypatch):
    calls = []
    def fake_timestamp(value, *, local_time):
        calls.append((value, local_time))
        return f"display:{value}"
    monkeypatch.setattr(diagnostics, "_event_display_timestamp", fake_timestamp)
    result = diagnostics.format_faults([fault_record()], local_time=True)
    assert result.startswith("display:2026-11-13T10:01:02.123456Z  CP-1  C0")
    assert "charger_at=display:2026-11-13T10:00:58Z" in result
    assert calls == [
        ("2026-11-13T10:01:02.123456Z", True),
        ("2026-11-13T10:00:58Z", True),
    ]


@pytest.mark.parametrize(("connector_id", "error_code", "expected"), [
    (None, None, "C?  Faulted  UnknownError"),
    (1, "NoError", "C1  Faulted  NoError"),
    (0, "InternalError", "C0  Faulted  InternalError"),
])
def test_fault_formatter_keeps_legacy_placeholder_rules(connector_id, error_code, expected):
    record = fault_record()
    record.update(connector_id=connector_id, error_code=error_code)
    assert expected in diagnostics.format_faults([record])
