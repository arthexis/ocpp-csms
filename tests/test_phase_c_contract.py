"""Phase C negative contract checks, run by existing CSMS pytest matrices.

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
        "C1: cached dependency set can change under an old release"
    )


def test_installation_checks_wheel_requirements_before_cutover():
    source = (ROLE / "tasks/main.yml").read_text()
    checked = source.split("- name: Install OCPP CSMS application wheel", 1)[1]
    checked = checked.split("- name: Perform controlled OCPP CSMS handoff", 1)[0]
    assert "- -m\n      - pip\n      - check" in checked, (
        "C2: --no-deps wheel may activate with incompatible requirements"
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
