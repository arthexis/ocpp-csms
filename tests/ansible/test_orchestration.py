from .helpers import (
    DEFAULTS,
    TASKS,
    assert_task_order,
    read,
    role_text,
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
    assert "rollover" not in role_text().lower()


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
        "Install live OCPP CSMS systemd unit",
    )


def test_replacement_health_is_proven_before_current_is_promoted():
    cutover = read(TASKS / "cutover.yml")

    assert_task_order(
        cutover,
        "Enable and start candidate OCPP CSMS service",
        "Wait for OCPP CSMS listener",
        "Verify candidate OCPP CSMS application status",
        "Verify previously connected chargers reconnect",
        "Promote verified release to current",
    )


def test_schema_upgrade_disables_automatic_runtime_rollback():
    cutover = read(TASKS / "cutover.yml")

    rollback_tasks = (
        "Stop failed replacement before rollback",
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


def test_candidate_unit_uses_valid_service_filename_and_is_verified_early():
    defaults = read(DEFAULTS)
    main = read(TASKS / "main.yml")

    assert 'ocpp_csms_candidate_dir: "{{ ocpp_csms_prefix }}/candidate"' in defaults
    assert (
        'ocpp_csms_candidate_service_path: '
        '"{{ ocpp_csms_candidate_dir }}/{{ ocpp_csms_service_name }}"'
    ) in defaults
    assert ".service.next" not in defaults

    assert_task_order(
        main,
        "Render candidate OCPP CSMS systemd unit",
        "Verify candidate systemd unit before handoff",
        "Run final install safety preflight",
    )
    assert "systemd-analyze" in task_section(
        main, "Verify candidate systemd unit before handoff"
    )


def test_candidate_runs_directly_from_immutable_release():
    template = read(
        DEFAULTS.parent.parent / "templates" / "ocpp-csms.service.j2"
    )

    assert "{{ ocpp_csms_release_command }}" in template
    assert "{{ ocpp_csms_runtime_command }}" not in template


def test_initialized_storage_is_validated_before_service_start():
    cutover = read(TASKS / "cutover.yml")

    assert_task_order(
        cutover,
        "Initialize OCPP CSMS storage with candidate release",
        "Inspect initialized OCPP CSMS database",
        "Inspect initialized OCPP CSMS transaction archive",
        "Verify OCPP CSMS data directory is writable",
        "Enable and start candidate OCPP CSMS service",
    )


def test_stable_command_is_exposed_only_after_handoff_processing():
    main = read(TASKS / "main.yml")

    assert_task_order(
        main,
        "Perform controlled OCPP CSMS handoff",
        "Install stable OCPP CSMS command",
        "Resolve stable OCPP CSMS command target",
        "Resolve active OCPP CSMS release command",
        "Assert stable OCPP CSMS command target",
    )
    assert "/usr/local/bin/ocpp-csms" in read(DEFAULTS)


def test_stable_command_assertion_uses_canonical_paths():
    main = read(TASKS / "main.yml")

    stable = task_section(main, "Resolve stable OCPP CSMS command target")
    active = task_section(main, "Resolve active OCPP CSMS release command")
    assertion = task_section(main, "Assert stable OCPP CSMS command target")

    assert "readlink" in stable and "- -f" in stable
    assert "readlink" in active and "- -f" in active
    assert "lnk_source" not in assertion
    assert "ocpp_csms_command_resolved.stdout" in assertion
    assert "ocpp_csms_release_command_resolved.stdout" in assertion


def test_base_role_refuses_root_deployment_identity():
    validation = task_section(
        read(TASKS / "main.yml"), "Validate supported OCPP CSMS appliance host"
    )

    assert "ansible_user_id != 'root'" in validation
    assert "ansible_user_uid | int != 0" in validation
    assert "Use Ansible become for privileged host changes" in validation
