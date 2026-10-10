"""Incoming OCPP 1.6 firmware/diagnostics progress and failure alert coverage."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ocpp_csms.session.handlers import SessionHandlers
from ocpp_csms.evidence.alerts import classify_event


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "handler", "status", "response_type"), [
    ("DiagnosticsStatusNotification", "on_diagnostics_status_notification", "Uploading", "DiagnosticsStatusNotificationPayload"),
    ("DiagnosticsStatusNotification", "on_diagnostics_status_notification", "UploadFailed", "DiagnosticsStatusNotificationPayload"),
    ("FirmwareStatusNotification", "on_firmware_status_notification", "Downloading", "FirmwareStatusNotificationPayload"),
    ("FirmwareStatusNotification", "on_firmware_status_notification", "InstallationFailed", "FirmwareStatusNotificationPayload"),
])
async def test_unsolicited_notifications_are_recorded_and_acknowledged(action, handler, status, response_type):
    recorded = []
    target = SimpleNamespace(_record=lambda name, payload: recorded.append((name, payload)))
    result = await getattr(SessionHandlers, handler)(target, status=status)
    assert type(result).__name__ == response_type
    assert recorded == [(action, {"status": status})]


@pytest.mark.parametrize(("action", "status", "category"), [
    ("DiagnosticsStatusNotification", "UploadFailed", "diagnostics"),
    ("FirmwareStatusNotification", "DownloadFailed", "firmware"),
    ("FirmwareStatusNotification", "InstallationFailed", "firmware"),
])
def test_failure_is_an_alert(action, status, category):
    event = {"kind": "ocpp", "direction": "in", "action": action, "id": 7,
             "payload": {"status": status}, "charger_id": "CP1"}
    alert = classify_event(event)
    assert alert is not None
    assert alert["category"] == category
    assert alert["details"]["status"] == status
    assert alert["source_event_id"] == 7


@pytest.mark.parametrize(("action", "status"), [
    ("DiagnosticsStatusNotification", "Idle"),
    ("DiagnosticsStatusNotification", "Uploading"),
    ("DiagnosticsStatusNotification", "Uploaded"),
    ("FirmwareStatusNotification", "Idle"),
    ("FirmwareStatusNotification", "Downloading"),
    ("FirmwareStatusNotification", "Downloaded"),
    ("FirmwareStatusNotification", "Installing"),
    ("FirmwareStatusNotification", "Installed"),
])
def test_normal_progress_not_alert(action, status):
    assert classify_event({"kind": "ocpp", "direction": "in", "action": action,
                           "payload": {"status": status}}) is None


def test_outgoing_failure_not_classified_as_incoming_alert():
    assert classify_event({"kind": "ocpp", "direction": "out",
                           "action": "FirmwareStatusNotification",
                           "payload": {"status": "DownloadFailed"}}) is None
