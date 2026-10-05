from .helpers import (
    DEFAULTS,
    TASKS,
    all_ansible_text,
    assert_task_order,
    read,
    task_section,
)


def test_safety_gates_bracket_staging_and_handoff():
    main = read(TASKS / "main.yml")

    for initial_gate in (
        "Run local initial install safety preflight",
        "Run remote initial install safety preflight",
    ):
        assert_task_order(
            main,
            initial_gate,
            "Install Python prerequisites",
            "Run final install safety preflight",
            "Perform controlled OCPP CSMS handoff",
        )


def test_retired_rollover_surface_is_absent():
    assert "rollover" not in all_ansible_text().lower()


def test_handoff_captures_reconnect_baseline_before_downtime():
    cutover = read(TASKS / "cutover.yml")

    assert_task_order(
        cutover,
        "Capture charger reconnect baseline",
        "Stop existing OCPP CSMS service",
    )


def test_schema_upgrade_happens_only_after_old_service_stops():
    cutover = read(TASKS / "cutover.yml")

    assert_task_order(
        cutover,
        "Stop existing OCPP CSMS service",
        "Upgrade database schema after service stop",
        "Activate immutable release",
    )


def test_replacement_health_is_proven_before_handoff_succeeds():
    cutover = read(TASKS / "cutover.yml")

    assert_task_order(
        cutover,
        "Enable and start activated OCPP CSMS service",
        "Wait for OCPP CSMS listener",
        "Verify OCPP CSMS application status",
        "Verify previously connected chargers reconnect",
    )


def test_schema_upgrade_disables_automatic_runtime_rollback():
    cutover = read(TASKS / "cutover.yml")

    rollback_tasks = (
        "Stop failed replacement before rollback",
        "Restore previous current release",
        "Restore previous live systemd unit",
        "Restart previous OCPP CSMS service",
        "Verify rollback listener",
        "Report handoff failure after safe rollback",
    )
    for name in rollback_tasks:
        assert "ocpp_csms_schema_action != 'upgrade'" in task_section(cutover, name)

    no_rollback = task_section(cutover, "Report handoff failure without automatic rollback")
    assert "ocpp_csms_schema_action == 'upgrade'" in no_rollback
    assert "the database schema was upgraded" in no_rollback


def test_converged_path_is_explicitly_non_disruptive():
    cutover = read(TASKS / "cutover.yml")

    assert "when: ocpp_csms_activation_required | bool" in task_section(
        cutover, "Activate and verify OCPP CSMS handoff"
    )

    for name in (
        "Ensure converged OCPP CSMS service is enabled and running",
        "Verify converged OCPP CSMS listener",
        "Verify converged OCPP CSMS application status",
    ):
        assert "when: not (ocpp_csms_activation_required | bool)" in task_section(
            cutover, name
        )


def test_candidate_unit_is_verified_before_final_handoff_gate_when_possible():
    main = read(TASKS / "main.yml")

    assert_task_order(
        main,
        "Verify candidate systemd unit before handoff",
        "Run final install safety preflight",
    )
    assert "systemd-analyze" in task_section(
        main, "Verify candidate systemd unit before handoff"
    )


def test_initialized_storage_is_validated_before_service_start():
    cutover = read(TASKS / "cutover.yml")

    assert_task_order(
        cutover,
        "Initialize OCPP CSMS storage with active release",
        "Inspect initialized OCPP CSMS database",
        "Inspect initialized transaction archive",
        "Verify OCPP CSMS data directory is writable",
        "Enable and start activated OCPP CSMS service",
    )


def test_stable_command_is_exposed_only_after_handoff_processing():
    main = read(TASKS / "main.yml")

    assert_task_order(
        main,
        "Perform controlled OCPP CSMS handoff",
        "Install stable OCPP CSMS command",
        "Assert stable OCPP CSMS command target",
    )
    assert "/usr/local/bin/ocpp-csms" in read(DEFAULTS)


def test_base_role_refuses_root_deployment_identity():
    validation = task_section(
        read(TASKS / "main.yml"), "Validate supported OCPP CSMS appliance host"
    )

    assert "ansible_user_id != 'root'" in validation
    assert "ansible_user_uid | int != 0" in validation
    assert "Use Ansible become for privileged host changes" in validation
