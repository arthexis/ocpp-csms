import pytest

from ocpp_csms.config_report import REDACTED, configuration_snapshot, sensitive_configuration_key


def test_sensitive_configuration_keys_are_detected():
    assert sensitive_configuration_key("AuthorizationKey")
    assert sensitive_configuration_key("Vendor.ClientSecret")
    assert sensitive_configuration_key("wifi_psk")
    assert not sensitive_configuration_key("HeartbeatInterval")


def test_snapshot_preserves_readonly_and_masks_sensitive_values():
    snapshot = configuration_snapshot(
        {
            "configuration_key": [
                {"key": "HeartbeatInterval", "readonly": False, "value": "60"},
                {"key": "AuthorizationKey", "readonly": True, "value": "secret-value"},
            ],
            "unknown_key": ["VendorThing"],
        },
        charger="charger-a",
    )

    assert snapshot == {
        "charger": "charger-a",
        "configuration": [
            {"key": "HeartbeatInterval", "readonly": False, "value": "60"},
            {"key": "AuthorizationKey", "readonly": True, "value": REDACTED},
        ],
        "unknown": ["VendorThing"],
    }


def test_snapshot_can_explicitly_show_sensitive_values():
    snapshot = configuration_snapshot(
        {
            "configuration_key": [
                {"key": "AuthorizationKey", "readonly": False, "value": "secret-value"},
            ],
            "unknown_key": [],
        },
        charger="charger-a",
        show_sensitive=True,
    )

    assert snapshot["configuration"][0]["value"] == "secret-value"


def test_snapshot_rejects_malformed_configuration_rows():
    with pytest.raises(ValueError):
        configuration_snapshot(
            {"configuration_key": [{"key": "Bad", "readonly": "no", "value": "x"}]},
            charger="charger-a",
        )
