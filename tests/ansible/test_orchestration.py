from .helpers import (
    DEFAULTS,
    TASKS,
    assert_task_order,
    load_yaml,
    read,
    task_by_name,
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


def test_legacy_inspection_occurs_after_candidate_validation_before_handoff():
    main = read(TASKS / "main.yml")
    assert_task_order(
        main,
        "Verify candidate systemd unit before handoff",
        "Run final install safety preflight",
        "Inspect legacy Arthexis OCPP listener",
        "Perform controlled OCPP CSMS handoff",
    )


def test_legacy_detection_is_listener_owned_not_install_presence():
    legacy = read(TASKS / "legacy_arthexis.yml")
    assert "ActiveState,MainPID" in legacy
    assert "ss -H -ltnp" in legacy
    assert "ocpp_csms_legacy_listener_states" in legacy
    defaults = load_yaml(DEFAULTS)
    assert "arthexis.service" in defaults["ocpp_csms_legacy_arthexis_services"]
    assert "arthexis-web.service" in defaults["ocpp_csms_legacy_arthexis_services"]
    assert "arthexis-arthexis-arthexis.service" in defaults["ocpp_csms_legacy_arthexis_services"]
    assert 8888 in defaults["ocpp_csms_legacy_arthexis_ports"]
    assert "{{ ocpp_csms_port }}" in defaults["ocpp_csms_legacy_arthexis_ports"]


def test_legacy_takeover_tracks_the_actual_listener_port():
    stop = task_by_name(TASKS / "legacy_arthexis_stop.yml", "Wait for legacy Arthexis OCPP listener to release port")
    restore = task_by_name(TASKS / "legacy_arthexis_restore.yml", "Verify restored legacy Arthexis listener")
    assert stop["ansible.builtin.wait_for"]["port"] == "{{ ocpp_csms_legacy_arthexis_port }}"
    assert restore["ansible.builtin.wait_for"]["port"] == "{{ ocpp_csms_legacy_arthexis_port }}"


def test_handoff_captures_reconnect_baseline_before_downtime():
    cutover = read(TASKS / "cutover.yml")
    assert_task_order(
        cutover,
        "Capture charger reconnect baseline",
        "Stop legacy Arthexis listener at cutover",
        "Stop existing OCPP CSMS service",
    )


def test_legacy_service_is_disabled_only_after_verified_promotion():
    cutover = read(TASKS / "cutover.yml")
    assert_task_order(
        cutover,
        "Wait for OCPP CSMS listener",
        "Verify candidate OCPP CSMS application status",
        "Promote verified release to current",
        "Disable legacy Arthexis OCPP service after verified takeover",
    )


def test_failed_legacy_takeover_restores_old_listener():
    restore = read(TASKS / "legacy_arthexis_restore.yml")
    assert_task_order(
        restore,
        "Stop failed replacement before legacy Arthexis rollback",
        "Restore legacy Arthexis OCPP listener",
        "Verify restored legacy Arthexis listener",
    )
    failure = task_by_name(
        TASKS / "cutover.yml", "Report failed legacy Arthexis takeover after restoration"
    )
    assert failure["when"] == "ocpp_csms_legacy_arthexis_service | length > 0"


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


def test_schema_upgrade_disables_managed_release_runtime_rollback():
    cutover = TASKS / "cutover.yml"
    for name in (
        "Stop failed replacement before rollback",
        "Restore previous live systemd unit",
        "Restart previous OCPP CSMS service",
        "Verify rollback listener",
        "Report handoff failure after safe rollback",
    ):
        conditions = task_by_name(cutover, name)["when"]
        assert "ocpp_csms_schema_action != 'upgrade'" in conditions


def test_converged_path_is_non_disruptive():
    cutover = TASKS / "cutover.yml"
    activation = task_by_name(cutover, "Activate and verify OCPP CSMS handoff")
    assert activation["when"] == "ocpp_csms_activation_required | bool"

    for name in (
        "Ensure converged OCPP CSMS service is enabled and running",
        "Verify converged OCPP CSMS listener",
        "Verify converged OCPP CSMS application status",
    ):
        assert task_by_name(cutover, name)["when"] == "not (ocpp_csms_activation_required | bool)"


def test_candidate_unit_is_staged_and_verified_before_handoff():
    defaults = load_yaml(DEFAULTS)
    main = read(TASKS / "main.yml")

    assert defaults["ocpp_csms_candidate_dir"].endswith("/candidate")
    assert defaults["ocpp_csms_candidate_service_path"].endswith("/{{ ocpp_csms_service_name }}")
    assert_task_order(
        main,
        "Render candidate OCPP CSMS systemd unit",
        "Verify candidate systemd unit before handoff",
        "Run final install safety preflight",
    )
    verify = task_by_name(TASKS / "main.yml", "Verify candidate systemd unit before handoff")
    assert verify["ansible.builtin.command"]["argv"][0] == "systemd-analyze"


def test_candidate_runs_from_immutable_release():
    template = read(DEFAULTS.parent.parent / "templates" / "ocpp-csms.service.j2")
    assert "{{ ocpp_csms_release_command }}" in template


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


def test_stable_command_is_exposed_after_handoff():
    main = read(TASKS / "main.yml")
    defaults = load_yaml(DEFAULTS)
    assert_task_order(
        main,
        "Perform controlled OCPP CSMS handoff",
        "Install stable OCPP CSMS command",
        "Assert stable OCPP CSMS command target",
    )
    assert defaults["ocpp_csms_command_path"] == "/usr/local/bin/ocpp-csms"


def test_base_role_refuses_root_deployment_identity():
    validation = task_by_name(TASKS / "main.yml", "Validate supported OCPP CSMS appliance host")
    conditions = validation["ansible.builtin.assert"]["that"]
    assert "ansible_user_id != 'root'" in conditions
    assert "ansible_user_uid | int != 0" in conditions
