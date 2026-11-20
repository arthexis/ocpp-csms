"""Hermetic regression for the measured CSMS charger interruption window."""
from pathlib import Path

CUTOVER = Path(__file__).resolve().parents[1] / "ansible/roles/ocpp_csms/tasks/cutover.yml"


def test_charger_interruption_timer_starts_after_discover_quiescence():
    source = CUTOVER.read_text()
    assert source.index("Refuse writer handoff while Discover remains active") < source.index(
        "Start critical handoff timer"
    ) < source.index("Stop existing OCPP CSMS service")
    assert source.index("Verify candidate OCPP CSMS application status") < source.index(
        "End critical handoff timer after health verification"
    )


def test_sqlite_verification_is_not_repeated_while_chargers_are_disconnected():
    source = CUTOVER.read_text()
    offline = source.split("- name: Stop existing OCPP CSMS service", 1)[1].split(
        "- name: Enable and start candidate OCPP CSMS service", 1
    )[0]
    assert offline.count("- schema-verify") == 1
    assert offline.index("- schema-verify") < offline.index(
        "Mark candidate writer as potentially started before service start"
    )
    assert "BEGIN IMMEDIATE" in offline
    assert "Apply pending systemd daemon reload" in offline


def test_forward_only_recovery_remains_in_place():
    source = CUTOVER.read_text()
    assert "ocpp_csms_candidate_writer_may_have_started: true" in source
    assert "Refuse automatic rollback after activation or writer start" in source
    assert "Restore prior Discover after safe pre-writer CSMS recovery" in source
