"""Deployment-path contracts for Ansible refactors.

These are static regressions: they never operate on appliances.
"""
import pytest

from .helpers import ROOT, TASKS, read, task_by_name, assert_task_order, load_yaml

PLAYBOOKS = ROOT / "ansible/playbooks"


def test_first_install_needs_storage_init_before_start():
    cutover = read(TASKS / "cutover.yml")
    assert_task_order(cutover, "Remove stale previous service snapshot on first install",
                      "Install live OCPP CSMS systemd unit",
                      "Initialize OCPP CSMS storage with candidate release",
                      "Assert initialized OCPP CSMS storage",
                      "Enable and start candidate OCPP CSMS service")
    assert "not (ocpp_csms_live_service_state.stat.exists | default(false))" in cutover


def test_unchanged_redeploy_skips_disruptive_cutover():
    decide = task_by_name(TASKS / "cutover.yml", "Decide whether release activation is required")
    condition = decide["ansible.builtin.set_fact"]["ocpp_csms_activation_required"]
    assert "ocpp_csms_current_state.stat.lnk_source" in condition
    assert "ocpp_csms_release_dir" in condition
    assert "ocpp_csms_live_service_state.stat.checksum" in condition
    assert "ocpp_csms_candidate_service_state.stat.checksum" in condition
    cutover = task_by_name(TASKS / "cutover.yml", "Activate and verify OCPP CSMS handoff")
    assert cutover["when"] == "ocpp_csms_activation_required | bool"


def test_failed_cutover_restores_previous_release_only_when_safe():
    cutover = read(TASKS / "cutover.yml")
    assert_task_order(cutover, "Preserve previous live systemd unit",
                      "Stop existing OCPP CSMS service",
                      "Verify candidate OCPP CSMS application status",
                      "Promote verified release to current")
    assert "Restore previous live systemd unit" in cutover
    assert "Restart previous OCPP CSMS service" in cutover
    rollback = task_by_name(TASKS / "cutover.yml", "Restart previous OCPP CSMS service")
    conditions = str(rollback["when"])
    assert "ocpp_csms_schema_action != 'upgrade'" in conditions
    assert "ocpp_csms_current_state.stat.islnk" in conditions
    assert "ocpp_csms_live_service_state.stat.exists" in conditions
    assert "Report handoff failure without automatic rollback" in cutover


@pytest.mark.parametrize("profile", ("satellite", "delegate"))
def test_forwarder_profile_convergence_is_guarded(profile):
    tasks = load_yaml(PLAYBOOKS / f"{profile}.yml")[0]
    forwarder = next(role for role in tasks["roles"] if role["role"] == "ocpp_forwarder")
    assert "ocpp_forwarder_enabled | default(false) | bool" in forwarder["when"]
    assert "not (ocpp_csms_stage_only | default(false) | bool)" in forwarder["when"]
    shutdown = next(task for task in tasks["post_tasks"]
                    if task.get("ansible.builtin.import_tasks") == "tasks/disable-forwarder.yml")
    assert "not (ocpp_forwarder_enabled | default(false) | bool)" in shutdown["when"]
    assert "not (ocpp_csms_stage_only | default(false) | bool)" in shutdown["when"]


def test_excluded_forwarder_is_stopped_and_verified():
    shutdown = load_yaml(PLAYBOOKS / "tasks/disable-forwarder.yml")
    stop = next(task for task in shutdown if task["name"] == "Disable excluded Forwarder service")
    assert stop["ansible.builtin.systemd_service"] == {
        "name": "ocpp-forwarder.service", "enabled": False, "state": "stopped",
    }
    verify = next(task for task in shutdown if task["name"] == "Verify excluded Forwarder is not active")
    assert verify["changed_when"] is False
    assert verify["failed_when"] == "ocpp_forwarder_active_check.rc == 0"


def test_stage_only_stops_before_cutover_and_side_effect_roles():
    main = read(TASKS / "main.yml")
    assert_task_order(main, "Run final install safety preflight",
                      "Report staged OCPP CSMS handoff plan",
                      "End host after safe staging",
                      "Perform controlled OCPP CSMS handoff")
    assert task_by_name(TASKS / "main.yml", "End host after safe staging")["when"] == "ocpp_csms_stage_only | bool"
    delegate = load_yaml(PLAYBOOKS / "delegate.yml")[0]
    exposure = next(role for role in delegate["roles"] if role["role"] == "ocpp_expose")
    assert "not (ocpp_csms_stage_only | default(false) | bool)" in exposure["when"]


def test_public_delegate_checks_basic_auth_and_loopback_before_exposure():
    delegate = read(PLAYBOOKS / "delegate.yml")
    exposure = read(ROOT / "ansible/roles/ocpp_expose/tasks/main.yml")
    wrapper = read(ROOT / "deploy.sh")
    assert "Verify Basic auth is configured before cloud deployment" in delegate
    assert "ocpp_csms_host=127.0.0.1" in wrapper
    assert "ocpp_csms_host == '127.0.0.1'" in exposure
    assert "Reject anonymous public CSMS" in exposure
    assert "Verify certificate matches the exact public DNS hostname" in exposure
    assert "Promote validated public WSS site with rollback" in exposure
    assert "Restore previous nginx site" in exposure
