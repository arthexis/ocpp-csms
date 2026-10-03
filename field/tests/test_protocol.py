import sqlite3

import pytest

from field.protocol import configuration_map, configuration_payload, evidence_checkpoint, reboot_observation
from ocpp_csms.events import EventStore


def test_configuration_payload_preserves_semantics():
    payload = configuration_payload(
        {
            "ok": True,
            "response": {
                "configuration_key": [
                    {"key": "HeartbeatInterval", "readonly": False, "value": "300"},
                    {"key": "SupportedFeatureProfiles", "readonly": True, "value": "Core"},
                ],
                "unknown_key": ["NotARealKey"],
            },
        }
    )

    assert configuration_map(payload) == {
        "HeartbeatInterval": (False, "300"),
        "SupportedFeatureProfiles": (True, "Core"),
    }
    assert payload["unknown_key"] == ["NotARealKey"]


def test_configuration_payload_rejects_failed_or_malformed_response():
    with pytest.raises(RuntimeError):
        configuration_payload({"error": "charger_not_connected"})
    with pytest.raises(ValueError):
        configuration_payload({"ok": True, "response": {"configuration_key": "bad"}})


def test_reboot_observation_only_uses_evidence_after_checkpoint(tmp_path):
    store = EventStore(tmp_path)
    store.record_runtime("charger_disconnected", charger_id="charger-a")
    store.record_ocpp("charger-a", "BootNotification", {}, direction="in")
    event_id, runtime_id = evidence_checkpoint(str(tmp_path))

    assert reboot_observation(
        str(tmp_path),
        "charger-a",
        after_event_id=event_id,
        after_runtime_id=runtime_id,
    ) == {
        "boot_notification": False,
        "heartbeat": False,
        "disconnect_seen": False,
        "reconnect_seen": False,
        "actions": [],
    }

    store.record_runtime("charger_disconnected", charger_id="charger-a")
    store.record_runtime("charger_connected", charger_id="charger-a")
    store.record_ocpp("charger-a", "BootNotification", {}, direction="in")
    store.record_ocpp("charger-a", "Heartbeat", {}, direction="in")

    observation = reboot_observation(
        str(tmp_path),
        "charger-a",
        after_event_id=event_id,
        after_runtime_id=runtime_id,
    )
    assert observation["disconnect_seen"] is True
    assert observation["reconnect_seen"] is True
    assert observation["boot_notification"] is True
    assert observation["heartbeat"] is True
