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


def test_authoritative_service_user_gate_replaces_redundant_stat_probes():
    source = CUTOVER.read_text()
    offline = source.split("- name: Stop existing OCPP CSMS service", 1)[1].split(
        "- name: Mark candidate writer as potentially started before service start", 1
    )[0]
    assert "Inspect initialized OCPP CSMS database" not in offline
    assert "Inspect initialized OCPP CSMS transaction archive" not in offline
    assert "Assert initialized OCPP CSMS storage" not in offline
    assert "database.is_file()" in offline
    assert "directory.is_dir()" in offline
    assert "os.access" in offline
    assert "?mode=rw" in offline
    assert "BEGIN IMMEDIATE" in offline
    assert offline.index("Verify selected storage before service activation") < offline.index(
        "Verify OCPP CSMS storage access as systemd service user"
    )


def test_live_systemd_unit_must_not_be_reloaded_before_old_writer_stops():
    source = CUTOVER.read_text()
    service = (CUTOVER.parents[1] / "templates/ocpp-csms.service.j2").read_text()
    assert "Restart=always" in service
    stop = source.index("- name: Stop existing OCPP CSMS service")
    install = source.index("- name: Install live OCPP CSMS systemd unit")
    reload = source.index("- name: Apply pending systemd daemon reload")
    writer = source.index("- name: Mark candidate writer as potentially started before service start")
    assert stop < install < reload < writer
    assert source.index("- name: Preserve previous live systemd unit") < stop
    assert source.index("- name: Restore previous systemd unit before candidate start") > writer
