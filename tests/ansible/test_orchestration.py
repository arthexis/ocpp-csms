from .helpers import (
    DEFAULTS,
    DEPLOY_SCRIPT,
    ROOT,
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


def test_incumbent_inspection_occurs_after_candidate_validation_before_handoff():
    main = read(TASKS / "main.yml")
    assert_task_order(
        main,
        "Verify candidate systemd unit before handoff",
        "Run final install safety preflight",
        "Inspect incumbent host-local OCPP service",
        "Perform controlled OCPP CSMS handoff",
    )


def test_incumbent_detection_uses_observed_traffic():
    incumbent = read(TASKS / "incumbent.yml")
    defaults = load_yaml(DEFAULTS)
    assert "ocpp_discover.incumbent" in incumbent
    assert "--interface" in incumbent
    assert "--seconds" in incumbent
    assert "--managed-service" in incumbent
    assert "ocpp_csms_incumbent_observe_seconds" in defaults


def test_incumbent_takeover_tracks_observed_listener_port():
    stop = task_by_name(TASKS / "incumbent_stop.yml", "Wait for incumbent OCPP listener to release port")
    restore = task_by_name(TASKS / "incumbent_restore.yml", "Verify restored incumbent OCPP listener")
    assert stop["ansible.builtin.wait_for"]["port"] == "{{ ocpp_csms_incumbent_port }}"
    assert restore["ansible.builtin.wait_for"]["port"] == "{{ ocpp_csms_incumbent_port }}"


def test_handoff_captures_reconnect_baseline_before_downtime():
    cutover = read(TASKS / "cutover.yml")
    assert_task_order(
        cutover,
        "Capture charger reconnect baseline",
        "Stop incumbent OCPP service listener at cutover",
        "Stop existing OCPP CSMS service",
    )


def test_incumbent_service_is_disabled_only_after_verified_promotion():
    cutover = read(TASKS / "cutover.yml")
    assert_task_order(
        cutover,
        "Wait for OCPP CSMS listener",
        "Verify candidate OCPP CSMS application status",
        "Promote verified release to current",
        "Disable incumbent OCPP service after verified takeover",
    )


def test_failed_incumbent_takeover_restores_old_listener():
    restore = read(TASKS / "incumbent_restore.yml")
    assert_task_order(
        restore,
        "Stop failed replacement before incumbent rollback",
        "Restore incumbent OCPP listener",
        "Verify restored incumbent OCPP listener",
    )
    failure = task_by_name(
        TASKS / "cutover.yml", "Report failed incumbent OCPP service takeover after restoration"
    )
    assert failure["when"] == "ocpp_csms_incumbent_service | length > 0"


def test_schema_upgrade_happens_only_after_old_service_stops():
    cutover = read(TASKS / "cutover.yml")
    assert_task_order(
        cutover,
        "Stop existing OCPP CSMS service",
        "Upgrade database schema after service stop",
        "Install live OCPP CSMS systemd unit",
    )


def test_incumbent_takeover_requires_a_real_charger_reconnect():
    reconnect = read(TASKS / "reconnect_with_discover.yml")
    assert "Probe incumbent charger reconnect before Discover recovery" in reconnect
    assert "wait-any" in reconnect
    assert "Verify incumbent charger reconnect after candidate Discover recovery" in reconnect
    assert "ocpp_csms_incumbent_service | length > 0" in reconnect


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


def test_stage_only_stops_before_handoff():
    main = read(TASKS / "main.yml")
    assert_task_order(
        main,
        "Inspect incumbent host-local OCPP service",
        "Inspect database schema cutover action",
        "Report staged OCPP CSMS handoff plan",
        "End host after safe staging",
        "Perform controlled OCPP CSMS handoff",
    )
    end_host = task_by_name(TASKS / "main.yml", "End host after safe staging")
    assert end_host["ansible.builtin.meta"] == "end_host"
    assert end_host["when"] == "ocpp_csms_stage_only | bool"


def test_diagnose_tasks_are_read_only_and_report_takeover_preflight():
    diagnose = read(TASKS / "diagnose.yml")
    assert "ocpp_discover.incumbent" in diagnose
    assert "ocpp_csms.install_preflight" in diagnose
    assert "diagnostics" in diagnose
    assert "takeover_preflight_passed" in diagnose
    for mutating_module in (
        "ansible.builtin.apt:",
        "ansible.builtin.copy:",
        "ansible.builtin.file:",
        "ansible.builtin.systemd_service:",
        "ansible.builtin.template:",
    ):
        assert mutating_module not in diagnose


def test_deploy_wrapper_exposes_field_friendly_options():
    wrapper = read(DEPLOY_SCRIPT)
    assert "--observe" in wrapper
    assert "ocpp_csms_incumbent_observe_seconds" in wrapper
    assert "--interface" in wrapper
    assert "ocpp_discover_interface" in wrapper
    assert "--reconnect" in wrapper
    assert "ocpp_csms_reconnect_timeout" in wrapper
    assert "--stage-only" in wrapper
    assert "ocpp_csms_stage_only=true" in wrapper
    assert "--diagnose" in wrapper
    assert "ansible/playbooks/diagnose.yml" in wrapper



def test_immutable_release_reuses_persistent_dependency_cache():
    main = read(TASKS / "main.yml")
    defaults = load_yaml(DEFAULTS)

    assert defaults["ocpp_csms_release_wheelhouse"].endswith("/wheelhouse")
    assert defaults["ocpp_csms_dependencies_dir"].endswith("/dependencies")
    assert defaults["ocpp_csms_dependency_cache"].endswith(
        "/{{ ocpp_csms_dependency_key }}"
    )
    assert defaults["ocpp_csms_dependency_specs"] == [
        "hatchling",
        "ocpp>=0.26,<1",
        "websockets>=12,<14",
    ]

    assert_task_order(
        main,
        "Create immutable release wheelhouse",
        "Check persistent dependency wheel cache",
        "Create persistent dependency wheel cache",
        "Download dependency wheels into persistent cache",
        "Mark persistent dependency wheel cache ready",
        "Build OCPP CSMS application wheel",
        "Find built OCPP CSMS wheel",
        "Validate built OCPP CSMS wheel",
        "Install OCPP CSMS into immutable release",
        "Verify immutable release command",
    )

    download = task_by_name(
        TASKS / "main.yml", "Download dependency wheels into persistent cache"
    )
    assert "ocpp_csms_dependency_specs" in str(
        download["ansible.builtin.command"]["argv"]
    )
    assert download["when"] == [
        "not ocpp_csms_release_ready.stat.exists",
        "not ocpp_csms_dependency_cache_ready.stat.exists",
    ]

    build = task_by_name(TASKS / "main.yml", "Build OCPP CSMS application wheel")
    assert "--no-deps" in build["ansible.builtin.command"]["argv"]
    assert build["environment"]["PIP_NO_INDEX"] == "1"
    assert build["environment"]["PIP_FIND_LINKS"] == "{{ ocpp_csms_dependency_cache }}"

    install = task_by_name(TASKS / "main.yml", "Install OCPP CSMS into immutable release")
    assert install["ansible.builtin.pip"]["name"] == "{{ ocpp_csms_built_wheels.files[0].path }}"
    extra_args = install["ansible.builtin.pip"]["extra_args"]
    assert "--no-index" in extra_args
    assert "--find-links {{ ocpp_csms_release_wheelhouse }}" in extra_args
    assert "--find-links {{ ocpp_csms_dependency_cache }}" in extra_args


def test_ansible_reports_elapsed_time_for_each_task():
    config = read(ROOT / "ansible.cfg")
    callback = read(ROOT / "ansible" / "callback_plugins" / "task_timing.py")

    assert "callback_plugins = ansible/callback_plugins" in config
    assert "callbacks_enabled = task_timing" in config
    assert 'CALLBACK_TYPE = "aggregate"' in callback
    assert 'CALLBACK_NAME = "task_timing"' in callback
    assert "time.monotonic()" in callback
    assert 'TIMING [{task.get_name()}] {elapsed:.2f}s' in callback
