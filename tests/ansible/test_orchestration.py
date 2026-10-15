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


def test_incumbent_disabled_only_after_candidate_verification():
    """Inspect actual systemd action and its position after health checks."""
    tasks = load_yaml(TASKS / "cutover.yml")
    handoff = next(task for task in tasks if "block" in task)
    steps = handoff["block"]
    disable = next(
        i for i, task in enumerate(steps)
        if task.get("ansible.builtin.systemd_service", {}).get("enabled") is False
    )
    checks = [
        i for i, task in enumerate(steps)
        if task.get("ansible.builtin.wait_for", {}).get("state") == "started"
        or (task.get("ansible.builtin.command", {}).get("argv", [])[-1:] == ["status"])
    ]
    assert checks
    assert max(checks) < disable
    assert steps[disable]["when"] == "ocpp_csms_incumbent_service | length > 0"


def test_incumbent_takeover_requires_a_real_charger_reconnect():
    reconnect = read(TASKS / "reconnect.yml")
    assert "Verify takeover charger reconnect" in reconnect
    assert "wait-any" in reconnect
    assert "ocpp_csms_incumbent_service | length > 0" in reconnect




def test_active_healthy_commit_is_noop(tmp_path):
    from ocpp_csms.install_environment import inspect_deployment

    releases = tmp_path / "releases"
    target = releases / "abc123def456"
    target.mkdir(parents=True)
    (tmp_path / "current").symlink_to(target, target_is_directory=True)
    decision = inspect_deployment(tmp_path / "current", target, service_healthy=True)
    assert decision == "skip"


def test_inactive_release_does_not_skip(tmp_path):
    from ocpp_csms.install_environment import inspect_deployment

    releases = tmp_path / "releases"
    target = releases / "abc123def456"
    other = releases / "fed654cba321"
    target.mkdir(parents=True)
    other.mkdir(parents=True)
    (tmp_path / "current").symlink_to(other, target_is_directory=True)
    assert inspect_deployment(tmp_path / "current", target, service_healthy=True) == "install"


def test_unhealthy_active_commit_is_not_success(tmp_path):
    from ocpp_csms.install_environment import inspect_deployment

    target = tmp_path / "releases" / "abc123def456"
    target.mkdir(parents=True)
    (tmp_path / "current").symlink_to(target, target_is_directory=True)
    assert inspect_deployment(tmp_path / "current", target, service_healthy=False) != "skip"


def test_missing_or_broken_active_release_is_not_success(tmp_path):
    from ocpp_csms.install_environment import inspect_deployment

    target = tmp_path / "releases" / "abc123def456"
    target.mkdir(parents=True)
    current = tmp_path / "current"
    assert inspect_deployment(current, target, service_healthy=True) == "install"
    current.symlink_to(tmp_path / "missing", target_is_directory=True)
    assert inspect_deployment(current, target, service_healthy=True) == "install"


def test_environment_validation_uses_real_interpreter_and_detects_missing_packages(tmp_path):
    import venv
    from ocpp_csms.install_environment import inspect_environment

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    python = venv_dir / "bin" / "python"
    assert inspect_environment(python, required_modules=("sys", "json")) is True
    assert inspect_environment(python, required_modules=("module_that_is_not_installed_abc123",)) is False
    assert inspect_environment(venv_dir / "missing-python", required_modules=("sys",)) is False


def test_environment_validation_is_read_only(tmp_path):
    import venv
    from ocpp_csms.install_environment import inspect_environment

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    before = sorted(str(p.relative_to(venv_dir)) for p in venv_dir.rglob("*"))
    assert inspect_environment(venv_dir / "bin" / "python", required_modules=("sys",)) is True
    after = sorted(str(p.relative_to(venv_dir)) for p in venv_dir.rglob("*"))
    assert after == before



def test_candidate_unit_is_staged_and_verified_before_handoff():
    main = read(TASKS / "main.yml")

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
        "Verify selected storage before service activation",
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



def test_release_environment_path_is_derived_from_git_commit():
    defaults = load_yaml(DEFAULTS)
    assert defaults["ocpp_csms_release_dir"] == "{{ ocpp_csms_releases_dir }}/{{ ocpp_csms_release_id }}"
    assert defaults["ocpp_csms_release_venv"] == "{{ ocpp_csms_release_dir }}/venv"
    main = read(TASKS / "main.yml")
    assert "rev-parse" in main
    assert "--short=12" in main
    assert ".ready" not in main

def test_runtime_dependencies_are_verified_without_readiness_markers():
    main = read(TASKS / "main.yml")
    assert "ocpp_csms_release_ready" not in main
    assert "ocpp_csms_dependency_ready" not in main
    assert "Mark reusable runtime dependency environment ready" not in main
    assert "ocpp_csms_runtime_dependencies" in main
    assert "ansible.builtin.pip:" in main

def test_release_environment_does_not_depend_on_external_site_packages():
    main = read(TASKS / "main.yml")
    defaults = read(DEFAULTS)
    assert "ocpp_csms_dependencies.pth" not in main
    assert "ocpp_csms_dependency_site_packages" not in main
    assert "ocpp_csms_dependency_dir:" not in defaults
    assert "{{ ocpp_csms_release_venv }}" in main

def test_application_is_built_outside_release_venv_and_installed_as_wheel():
    build = task_by_name(TASKS / "main.yml", "Build OCPP CSMS application wheel")
    install = task_by_name(
        TASKS / "main.yml", "Install OCPP CSMS application wheel into immutable release"
    )

    argv = build["ansible.builtin.command"]["argv"]
    assert "--no-deps" in argv
    assert "--no-build-isolation" in argv

    assert install["ansible.builtin.pip"]["extra_args"] == "--no-index --no-deps"


def test_development_dependencies_are_release_local_and_optional_without_markers():
    main = read(TASKS / "main.yml")
    development = task_by_name(TASKS / "main.yml", "Install release development dependencies")
    assert development["ansible.builtin.pip"]["virtualenv"] == "{{ ocpp_csms_release_venv }}"
    assert "ocpp_csms_dev | bool" in development["when"]
    assert "ocpp_csms_release_ready" not in str(development)
    assert ".ready" not in main

def test_development_deploy_gets_distinct_immutable_release():
    capture = task_by_name(TASKS / "main.yml", "Capture immutable release identifier")
    release_id = capture["ansible.builtin.set_fact"]["ocpp_csms_release_id"]

    assert "'-dev' if ocpp_csms_dev | bool else ''" in release_id


def test_ansible_reports_elapsed_time_for_each_task():
    config = read(ROOT / "ansible.cfg")
    callback = read(ROOT / "ansible" / "callback_plugins" / "task_timing.py")

    assert "callback_plugins = ansible/callback_plugins" in config
    assert "callbacks_enabled = task_timing" in config
    assert 'CALLBACK_TYPE = "aggregate"' in callback
    assert 'CALLBACK_NAME = "task_timing"' in callback
    assert "time.monotonic()" in callback
    assert 'TIMING [{task.get_name()}] {elapsed:.2f}s' in callback


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
