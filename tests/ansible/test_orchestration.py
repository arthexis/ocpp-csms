import sqlite3
import subprocess
import sys

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
        "Upgrade schema and enable WAL after service stop",
        "Install live OCPP CSMS systemd unit",
    )


def test_incumbent_takeover_requires_a_real_charger_reconnect():
    reconnect = read(TASKS / "reconnect.yml")
    assert "Verify takeover charger reconnect" in reconnect
    assert "wait-any" in reconnect
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


def test_initialized_storage_is_validated_before_service_start():
    cutover = read(TASKS / "cutover.yml")
    assert_task_order(
        cutover,
        "Initialize OCPP CSMS storage with candidate release",
        "Inspect initialized OCPP CSMS database",
        "Inspect initialized OCPP CSMS transaction archive",
        "Assert initialized OCPP CSMS storage",
        "Verify OCPP CSMS storage access as systemd service user",
        "Enable and start candidate OCPP CSMS service",
    )



def test_storage_probe_uses_service_identity_and_checks_sqlite_writer_access():
    # Assert the security boundary and SQLite write transaction, not the
    # names of Ansible variables used to supply database paths.
    task = task_by_name(TASKS / "cutover.yml", "Verify OCPP CSMS storage access as systemd service user")
    assert task["become"] is True
    assert task["become_user"] == "{{ ocpp_csms_service_user }}"
    assert task["changed_when"] is False
    argv = task["ansible.builtin.command"]["argv"]
    script = argv[argv.index("-c") + 1]
    assert "mode=rw" in script
    assert "BEGIN IMMEDIATE" in script
    assert "connection.rollback()" in script



def test_storage_probe_executes_read_write_check_without_persisting_changes(tmp_path):
    """Exercise the embedded probe against real SQLite, not variable spelling."""
    task = task_by_name(TASKS / "cutover.yml", "Verify OCPP CSMS storage access as systemd service user")
    argv = task["ansible.builtin.command"]["argv"]
    script = argv[argv.index("-c") + 1]

    archives = tmp_path / "transactions"
    archives.mkdir()
    database = tmp_path / "ocpp-csms.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO evidence VALUES (1)")

    command = [sys.executable, "-c", script, str(tmp_path), str(database), str(archives)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id FROM evidence").fetchall() == [(1,)]

    # A missing database must not be silently created by the readiness probe.
    database.unlink()
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert not database.exists()



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



def test_stage_only_conditions_exclude_storage_preparation_and_handoff():
    """Evaluate staging conditions for both modes, rather than snapshot strings."""
    from jinja2 import Environment

    tasks = load_yaml(TASKS / "main.yml")
    prepare = next(task for task in tasks if task.get("name") == "Prepare versioned candidate before service downtime")
    end_host = next(task for task in tasks if task.get("name") == "End host after safe staging")
    handoff = next(task for task in tasks if task.get("name") == "Perform controlled OCPP CSMS handoff")

    environment = Environment()
    # Plain Jinja2 lacks Ansible's bool filter; these inputs are actual booleans.
    environment.filters["bool"] = bool

    def enabled(task, **values):
        conditions = task.get("when", [])
        if isinstance(conditions, str):
            conditions = [conditions]
        return all(environment.compile_expression(condition)(**values) for condition in conditions)

    for action in ("create", "reuse"):
        for stage_only in (True, False):
            values = {
                "ocpp_csms_stage_only": stage_only,
                "ocpp_csms_storage_selection": {"action": action},
            }
            assert enabled(prepare, **values) == (not stage_only and action == "create")
            assert enabled(end_host, **values) == stage_only

    # Ansible's end_host is a control-flow barrier, not a conditional on handoff.
    assert end_host["ansible.builtin.meta"] == "end_host"
    assert "when" not in handoff
    assert tasks.index(end_host) < tasks.index(handoff)



def test_diagnose_tasks_are_read_only_and_report_takeover_preflight():
    diagnose = read(TASKS / "diagnose.yml")
    assert "ocpp_discover.incumbent" in diagnose
    assert "ocpp_csms.install_preflight" in diagnose
    assert "diagnostics" in diagnose
    assert "safety_preflight_passed" in diagnose
    assert "incumbent_observation_succeeded" in diagnose
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
    assert "--interface" in wrapper
    assert "--reconnect" in wrapper
    assert "--stage-only" in wrapper
    assert "ocpp_csms_stage_only=true" in wrapper
    assert "--diagnose" in wrapper
    assert "ansible/playbooks/diagnose.yml" in wrapper
    assert "--dev" in wrapper
    assert "ocpp_csms_dev=true" in wrapper



def test_reusable_environments_are_keyed_by_requirements_and_platform():
    defaults = load_yaml(DEFAULTS)

    assert defaults["ocpp_csms_build_requirements"] == ["setuptools>=68"]
    assert defaults["ocpp_csms_runtime_dependencies"] == [
        "websockets>=12,<14",
        "ocpp>=0.26,<1",
    ]
    assert "hash('sha256')" in read(DEFAULTS)
    assert defaults["ocpp_csms_builds_dir"].endswith("/builds")
    assert defaults["ocpp_csms_dependencies_dir"].endswith("/dependencies")


def test_runtime_dependencies_are_installed_once_from_defaults():
    defaults = load_yaml(DEFAULTS)
    install = task_by_name(TASKS / "main.yml", "Install reusable runtime dependencies")
    ready = task_by_name(TASKS / "main.yml", "Mark reusable runtime dependency environment ready")
    assert install["ansible.builtin.pip"] == {
        "name": "{{ ocpp_csms_runtime_dependencies }}",
        "virtualenv": "{{ ocpp_csms_dependency_venv }}",
    }
    assert defaults["ocpp_csms_runtime_dependencies"] == ["websockets>=12,<14", "ocpp>=0.26,<1"]
    for task in (install, ready):
        assert "not ocpp_csms_dependency_ready.stat.exists" in task["when"]


def test_release_reuses_runtime_site_packages_without_mutating_dependency_environment():
    dependency_site = task_by_name(
        TASKS / "main.yml", "Resolve reusable runtime dependency site packages"
    )
    release_site = task_by_name(TASKS / "main.yml", "Resolve release site packages")
    link = task_by_name(TASKS / "main.yml", "Link reusable runtime dependencies into release")

    assert dependency_site["changed_when"] is False
    assert release_site["changed_when"] is False
    assert link["ansible.builtin.copy"]["dest"].endswith("/ocpp_csms_dependencies.pth")


def test_application_is_built_outside_release_venv_and_installed_as_wheel():
    build = task_by_name(TASKS / "main.yml", "Build OCPP CSMS application wheel")
    install = task_by_name(
        TASKS / "main.yml", "Install OCPP CSMS application wheel into immutable release"
    )

    argv = build["ansible.builtin.command"]["argv"]
    assert "--no-deps" in argv
    assert "--no-build-isolation" in argv

    assert install["ansible.builtin.pip"]["extra_args"] == "--no-index --no-deps"


def test_development_dependencies_remain_release_local_and_opt_in():
    development = task_by_name(TASKS / "main.yml", "Install release development dependencies")

    assert development["ansible.builtin.pip"] == {
        "name": [
            "pytest>=8,<9",
            "pytest-asyncio>=0.23,<2",
            "pytest-xdist>=3,<4",
        ],
        "virtualenv": "{{ ocpp_csms_release_venv }}",
    }
    assert development["when"] == [
        "not ocpp_csms_release_ready.stat.exists",
        "ocpp_csms_install_mode == 'online'",
        "ocpp_csms_dev | bool",
    ]


def test_development_deploy_gets_distinct_immutable_release():
    capture = task_by_name(TASKS / "main.yml", "Capture immutable release identifier")
    release_id = capture["ansible.builtin.set_fact"]["ocpp_csms_release_id"]

    assert "'-dev' if ocpp_csms_dev | bool else ''" in release_id


def test_release_install_phases_precede_verification_and_readiness():
    main = read(TASKS / "main.yml")

    assert_task_order(
        main,
        "Check reusable build environment",
        "Install reusable build requirements",
        "Check reusable runtime dependency environment",
        "Install reusable runtime dependencies",
        "Link reusable runtime dependencies into release",
        "Build OCPP CSMS application wheel",
        "Install OCPP CSMS application wheel into immutable release",
        "Verify immutable release command",
        "Mark immutable release ready",
        "Render candidate OCPP CSMS systemd unit",
    )

    verify = task_by_name(TASKS / "main.yml", "Verify immutable release command")
    assert verify["changed_when"] is False


def test_ansible_reports_elapsed_time_for_each_task():
    config = read(ROOT / "ansible.cfg")
    callback = read(ROOT / "ansible" / "callback_plugins" / "task_timing.py")

    assert "callback_plugins = ansible/callback_plugins" in config
    assert "callbacks_enabled = task_timing" in config
    assert 'CALLBACK_TYPE = "aggregate"' in callback
    assert 'CALLBACK_NAME = "task_timing"' in callback
    assert "time.monotonic()" in callback
    assert 'TIMING [{task.get_name()}] {elapsed:.2f}s' in callback


def test_wal_preparation_runs_for_existing_current_schema_before_activation():
    cutover = TASKS / "cutover.yml"
    task = task_by_name(cutover, "Upgrade schema and enable WAL after service stop")
    assert task["when"] == "ocpp_csms_schema_action != 'create'"
    assert "schema-upgrade" in task["ansible.builtin.command"]["argv"]
    verify = task_by_name(cutover, "Verify database schema and SQLite integrity before activation")
    assert verify["when"] == "ocpp_csms_schema_action != 'create'"
    assert "schema-verify" in verify["ansible.builtin.command"]["argv"]
    assert_task_order(
        read(cutover),
        "Stop existing OCPP CSMS service",
        "Upgrade schema and enable WAL after service stop",
        "Verify database schema and SQLite integrity before activation",
        "Install live OCPP CSMS systemd unit",
    )


def test_converged_deployment_verifies_wal_without_live_conversion():
    cutover = TASKS / "cutover.yml"
    verify = task_by_name(cutover, "Verify converged database WAL and SQLite integrity")
    assert verify["when"] == "not (ocpp_csms_activation_required | bool)"
    assert verify["changed_when"] is False
    assert "schema-verify" in verify["ansible.builtin.command"]["argv"]
    assert_task_order(
        read(cutover),
        "Ensure converged OCPP CSMS service is enabled and running",
        "Verify converged OCPP CSMS application status",
        "Verify converged database WAL and SQLite integrity",
    )


def test_offline_skips_only_post_start_reconnection():
    defaults = load_yaml(DEFAULTS)
    assert defaults["ocpp_csms_offline"] is False
    cutover = read(TASKS / "cutover.yml")
    reconnect = task_by_name(TASKS / "cutover.yml", "Verify previously connected chargers reconnect")
    assert reconnect["when"] == "not (ocpp_csms_offline | bool)"
    assert_task_order(
        cutover,
        "Capture charger reconnect baseline",
        "Stop existing OCPP CSMS service",
        "Wait for OCPP CSMS listener",
        "Verify candidate OCPP CSMS application status",
        "Verify previously connected chargers reconnect",
    )
    assert "ocpp_csms_offline" not in read(TASKS / "main.yml")
    assert "ocpp_csms_offline" not in read(TASKS / "reconnect.yml")


def test_deploy_offline_flag_is_forwarded_to_ansible():
    deploy = read(DEPLOY_SCRIPT)
    assert "--offline)" in deploy
    assert "offline=0" in deploy
    assert 'set -- -e ocpp_csms_offline=true "$@"' in deploy
    assert "--offline does not apply to --diagnose." in deploy
