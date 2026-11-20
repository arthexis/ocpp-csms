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


def test_service_identity_gate_preserves_regular_file_and_directory_contract(tmp_path):
    import sqlite3
    import subprocess
    import sys
    import textwrap

    source = CUTOVER.read_text()
    gate = source.split(
        "- name: Verify OCPP CSMS storage access as systemd service user", 1
    )[1].split("          - |", 1)[1].split(
        '          - "{{ ocpp_csms_service_data_dir }}"', 1
    )[0]
    code = textwrap.dedent(gate)
    data_dir = tmp_path / "schema"
    data_dir.mkdir()
    database = data_dir / "ocpp-csms.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE test_gate (id INTEGER)")
    archives = data_dir / "transactions"
    archives.mkdir()

    def check():
        return subprocess.run(
            [sys.executable, "-c", code, str(data_dir), str(database), str(archives)],
            capture_output=True, text=True, timeout=5,
        )

    assert check().returncode == 0
    original_database = data_dir / "real.sqlite3"
    database.rename(original_database)
    database.symlink_to(original_database)
    result = check()
    assert result.returncode != 0 and "cannot access database" in result.stderr
    database.unlink()
    original_database.rename(database)
    original_archives = data_dir / "real-transactions"
    archives.rename(original_archives)
    archives.symlink_to(original_archives, target_is_directory=True)
    result = check()
    assert result.returncode != 0 and "cannot access directory" in result.stderr
