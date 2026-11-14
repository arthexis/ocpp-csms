"""Immutable release, dependency compatibility and writer-rollback safety contracts.

End-to-end installation fixtures are added to the existing Ansible integration
runner; these structural invariants catch unsafe refactors earlier.
"""
from pathlib import Path

ROLE = Path(__file__).resolve().parents[1] / "ansible/roles/ocpp_csms"


def test_dependency_environment_is_scoped_to_immutable_release():
    source = (ROLE / "defaults/main.yml").read_text()
    key = source.split("ocpp_csms_dependency_key: >-", 1)[1].split(
        "ocpp_csms_dependencies_dir:", 1
    )[0]
    assert "ocpp_csms_release_id" in key, (
        "cached dependency set can change under an old release"
    )
    location = source.split("ocpp_csms_dependency_dir:", 1)[1].splitlines()[0]
    assert "ocpp_csms_release_dir" in location, (
        "an orphaned external dependency venv would survive release pruning"
    )


def test_installation_checks_wheel_requirements_before_cutover():
    source = (ROLE / "tasks/main.yml").read_text()
    checked = source.split("- name: Install OCPP CSMS application wheel", 1)[1]
    checked = checked.split("- name: Perform controlled OCPP CSMS handoff", 1)[0]
    assert "- -m\n      - pip\n      - check" in checked, (
        "--no-deps wheel may activate with incompatible requirements"
    )


def test_any_candidate_writer_start_blocks_automatic_rollback():
    source = (ROLE / "tasks/cutover.yml").read_text()
    mark = source.split(
        "- name: Mark candidate writer as potentially started before service start",
        1,
    )[1].split("- name: Enable and start candidate OCPP CSMS service", 1)[0]
    assert "ocpp_csms_candidate_writer_may_have_started: true" in mark
    assert "when:" not in mark
    assert "not (ocpp_csms_candidate_writer_may_have_started" in source


def test_discover_quiescence_before_writer_handoff():
    source = (ROLE / "tasks/cutover.yml").read_text()
    assert source.index("Quiesce Discover before changing CSMS schema") < source.index(
        "Stop existing OCPP CSMS service"
    )
    assert source.index("Refuse writer handoff while Discover remains active") < source.index(
        "Stop existing OCPP CSMS service"
    )
    assert "ocpp_csms_active_data_dir != ocpp_csms_storage_selection.target" in source


def test_recovery_only_restores_discover_after_old_csms_and_before_writer():
    source = (ROLE / "tasks/cutover.yml").read_text()
    rescue = source.split("  rescue:", 1)[1]
    assert rescue.index("Refuse automatic rollback after activation or writer start") < \
        rescue.index("Restart previous managed service after pre-activation failure") < \
        rescue.index("Restore prior Discover after safe pre-writer CSMS recovery")
    restore = rescue.split("Restore prior Discover after safe pre-writer CSMS recovery", 1)[1]
    restore = restore.split("- name: Report unsuccessful pre-activation handoff", 1)[0]
    assert "ocpp_csms_discover_was_active | default(false) | bool" in restore
    assert "ocpp_csms_current_state.stat.islnk | default(false)" in restore
