"""Deployment-path contracts for Ansible refactors.

These are static regressions: they never operate on appliances.
"""
from .helpers import ROOT, TASKS, read, task_by_name, assert_task_order, load_yaml

PLAYBOOKS = ROOT / "ansible/playbooks"


def test_first_install_uses_prepared_storage_before_start():
    main = read(TASKS / "main.yml")
    prepare = read(TASKS / "storage_prepare.yml")
    cutover = read(TASKS / "cutover.yml")
    assert_task_order(main, "Inspect versioned storage plan for staged handoff",
                      "Select candidate service storage directory",
                      "Prepare versioned candidate before service downtime")
    assert_task_order(prepare, "Prepare new schema storage without activating it",
                      "Verify prepared schema storage")
    assert_task_order(cutover, "Verify selected storage before service activation",
                      "Install live OCPP CSMS systemd unit",
                      "Assert initialized OCPP CSMS storage",
                      "Enable and start candidate OCPP CSMS service")
    assert "not (ocpp_csms_live_service_state.stat.exists | default(false))" in cutover


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


def test_stage_only_prevents_public_exposure():
    """Keep the cross-role exposure gate; main-role staging is tested elsewhere."""
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
