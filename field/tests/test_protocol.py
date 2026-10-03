import pytest

from field.protocol import (
    REDACTED,
    configuration_map,
    configuration_payload,
    evidence_checkpoint,
    reboot_observation,
    sensitive_configuration_key,
)
from ocpp_csms.events import EventStore


@pytest.mark.parametrize(
    "response, error",
    [
        ({"error": "charger_not_connected"}, RuntimeError),
        ({"ok": True, "response": {"configuration_key": "bad"}}, ValueError),
    ],
)
def test_configuration_payload_rejects_invalid_results(response, error):
    with pytest.raises(error):
        configuration_payload(response)


def test_configuration_payload_preserves_non_sensitive_semantics():
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


@pytest.mark.parametrize(
    "key",
    [
        "AuthorizationKey",
        "defaultAuthorizationKey",
        "apPwd",
        "vpnPsk",
        "client_secret",
        "api-token",
        "PrivateKey",
        "tyKey",
        "rfidtagfreecharging",
    ],
)
def test_sensitive_configuration_keys_are_classified(key):
    assert sensitive_configuration_key(key) is True


@pytest.mark.parametrize(
    "key",
    [
        "HeartbeatInterval",
        "GetConfigurationMaxKeys",
        "SupportedFeatureProfiles",
        "MeterValueSampleInterval",
        "NumberOfConnectors",
    ],
)
def test_normal_configuration_keys_are_not_redacted(key):
    assert sensitive_configuration_key(key) is False


def test_configuration_payload_redacts_sensitive_values_before_json_evidence():
    payload = configuration_payload(
        {
            "ok": True,
            "response": {
                "configuration_key": [
                    {"key": "apPwd", "readonly": False, "value": "do-not-publish"},
                    {"key": "HeartbeatInterval", "readonly": False, "value": "60"},
                    {"key": "AuthorizationKey", "readonly": False, "value": "also-secret"},
                ],
                "unknown_key": [],
            },
        }
    )

    assert configuration_map(payload) == {
        "apPwd": (False, REDACTED),
        "HeartbeatInterval": (False, "60"),
        "AuthorizationKey": (False, REDACTED),
    }


def test_reboot_observation_ignores_old_evidence_and_accepts_new_sequence(tmp_path):
    store = EventStore(tmp_path)
    store.record_runtime("charger_disconnected", charger_id="charger-a")
    store.record_ocpp("charger-a", "BootNotification", {}, direction="in")
    event_id, runtime_id = evidence_checkpoint(str(tmp_path))

    before = reboot_observation(
        str(tmp_path),
        "charger-a",
        after_event_id=event_id,
        after_runtime_id=runtime_id,
    )
    assert not any(
        before[key]
        for key in ("disconnect_seen", "reconnect_seen", "boot_notification", "heartbeat")
    )

    store.record_runtime("charger_disconnected", charger_id="charger-a")
    store.record_runtime("charger_connected", charger_id="charger-a")
    store.record_ocpp("charger-a", "BootNotification", {}, direction="in")
    store.record_ocpp("charger-a", "Heartbeat", {}, direction="in")

    after = reboot_observation(
        str(tmp_path),
        "charger-a",
        after_event_id=event_id,
        after_runtime_id=runtime_id,
    )
    assert all(
        after[key]
        for key in ("disconnect_seen", "reconnect_seen", "boot_notification", "heartbeat")
    )
