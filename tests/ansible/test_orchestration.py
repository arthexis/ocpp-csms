import pytest

from .helpers import (
    DEFAULTS,
    DEPLOY_SCRIPT,
    PLAYBOOK,
    SERVICE_TEMPLATE,
    TASKS,
    assert_task_order,
    read,
    role_text,
    task_section,
)


@pytest.fixture(scope="module")
def main_tasks():
    return read(TASKS / "main.yml")


@pytest.fixture(scope="module")
def cutover_tasks():
    return read(TASKS / "cutover.yml")


def test_satellite_playbook_has_one_implemented_role():
    playbook = read(PLAYBOOK)
    assert "- role: ocpp_csms" in playbook
    assert "ocpp_discover" not in playbook
    assert "wireguard" not in playbook


def test_local_deploy_helper_uses_canonical_playbook_without_root():
    script = read(DEPLOY_SCRIPT)
    assert 'PLAYBOOK="$ROOT/ansible/playbooks/satellite.yml"' in script
    assert 'if [ "$(id -u)" -eq 0 ]' in script
    assert 'exec ansible-playbook "$PLAYBOOK" -i localhost, -c local "$@"' in script


def test_safety_gates_bracket_staging_and_handoff(main_tasks):
    for initial_gate in (
        "Run local initial install safety preflight",
        "Run remote initial install safety preflight",
    ):
        assert_task_order(
            main_tasks,
            initial_gate,
            "Install Python prerequisites",
            "Run final install safety preflight",
            "Perform controlled OCPP CSMS handoff",
        )


def test_remote_preflight_is_temporary_and_cleaned_before_staging(main_tasks):
    remote = task_section(main_tasks, "Run remote initial install safety preflight")
    assert "Create temporary remote preflight source directory" in remote
    assert "Execute remote initial install safety preflight" in remote
    assert "Remove temporary remote preflight source" in remote
    assert "always:" in remote


def test_retired_rollover_surface_is_absent():
    assert "rollover" not in role_text().lower()


def test_immutable_release_is_built_before_final_gate(main_tasks):
    assert_task_order(
        main_tasks,
        "Capture immutable release identifier",
        "Create immutable release directory",
        "Install OCPP CSMS into immutable release",
        "Mark immutable release ready",
        "Run final install safety preflight",
    )


def test_handoff_captures_reconnect_baseline_before_downtime(cutover_tasks):
    assert_task_order(
        cutover_tasks,
        "Capture charger reconnect baseline",
        "Stop existing OCPP CSMS service",
    )


def test_schema_upgrade_happens_only_after_old_service_stops(cutover_tasks):
    assert_task_order(
        cutover_tasks,
        "Stop existing OCPP CSMS service",
        "Upgrade database schema after service stop",
        "Activate immutable release",
    )


def test_replacement_health_is_proven_before_handoff_succeeds(cutover_tasks):
    assert_task_order(
        cutover_tasks,
        "Enable and start activated OCPP CSMS service",
        "Wait for OCPP CSMS listener",
        "Verify OCPP CSMS application status",
        "Verify previously connected chargers reconnect",
    )


@pytest.mark.parametrize(
    "task_name",
    (
        "Stop failed replacement before rollback",
        "Restore previous current release",
        "Restore previous live systemd unit",
        "Restart previous OCPP CSMS service",
        "Verify rollback listener",
        "Report handoff failure after safe rollback",
    ),
)
def test_runtime_rollback_requires_schema_compatibility(cutover_tasks, task_name):
    assert "ocpp_csms_schema_action != 'upgrade'" in task_section(cutover_tasks, task_name)


def test_schema_upgrade_failure_is_explicitly_not_auto_rolled_back(cutover_tasks):
    no_rollback = task_section(cutover_tasks, "Report handoff failure without automatic rollback")
    assert "ocpp_csms_schema_action == 'upgrade'" in no_rollback
    assert "the database schema was upgraded" in no_rollback


def test_converged_path_is_explicitly_non_disruptive(cutover_tasks):
    assert "when: ocpp_csms_activation_required | bool" in task_section(
        cutover_tasks, "Activate and verify OCPP CSMS handoff"
    )

    for name in (
        "Ensure converged OCPP CSMS service is enabled and running",
        "Verify converged OCPP CSMS listener",
        "Verify converged OCPP CSMS application status",
    ):
        assert "when: not (ocpp_csms_activation_required | bool)" in task_section(cutover_tasks, name)


def test_candidate_unit_is_verified_before_final_handoff_gate_when_possible(main_tasks):
    assert_task_order(
        main_tasks,
        "Verify candidate systemd unit before handoff",
        "Run final install safety preflight",
    )
    assert "systemd-analyze" in task_section(main_tasks, "Verify candidate systemd unit before handoff")


def test_initialized_storage_is_validated_before_service_start(cutover_tasks):
    assert_task_order(
        cutover_tasks,
        "Initialize OCPP CSMS storage with active release",
        "Inspect initialized OCPP CSMS database",
        "Inspect initialized OCPP CSMS transaction archive",
        "Verify OCPP CSMS data directory is writable",
        "Enable and start activated OCPP CSMS service",
    )


def test_stable_command_is_exposed_only_after_handoff_processing(main_tasks):
    assert_task_order(
        main_tasks,
        "Perform controlled OCPP CSMS handoff",
        "Install stable OCPP CSMS command",
        "Assert stable OCPP CSMS command target",
    )
    assert '/usr/local/bin/ocpp-csms' in read(DEFAULTS)


def test_service_template_follows_current_immutable_release():
    unit = read(SERVICE_TEMPLATE)
    defaults = read(DEFAULTS)
    assert 'ocpp_csms_runtime_command: "{{ ocpp_csms_current_link }}/venv/bin/ocpp-csms"' in defaults
    assert 'ExecStart="{{ ocpp_csms_runtime_command }}"' in unit
    assert "Restart=always" in unit


def test_base_role_refuses_unsupported_deployment_identity_and_platform(main_tasks):
    validation = task_section(main_tasks, "Validate supported OCPP CSMS appliance host")
    for invariant in (
        "ansible_user_id != 'root'",
        "ansible_user_uid | int != 0",
        "ansible_facts.os_family == 'Debian'",
        "ansible_facts.architecture in ['aarch64', 'arm64']",
    ):
        assert invariant in validation
    assert "Use Ansible become for privileged host changes" in validation
