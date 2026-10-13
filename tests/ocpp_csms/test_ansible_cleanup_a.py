"""Regression checks for profile-aware diagnostic and Forwarder convergence."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAYBOOKS = ROOT / "ansible/playbooks"
DIAGNOSE = ROOT / "ansible/roles/ocpp_csms/tasks/diagnose.yml"


def test_forwarder_disable_is_shared_and_fail_closed():
    shared = (PLAYBOOKS / "tasks/disable-forwarder.yml").read_text()
    assert "systemd_service:" in shared
    assert "state: stopped" in shared
    assert "enabled: false" in shared
    assert "ActiveState=inactive" in shared
    assert "UnitFileState=disabled" in shared
    assert "ocpp_forwarder_active_check.rc != 0" in shared
    for name in ("satellite.yml", "delegate.yml"):
        source = (PLAYBOOKS / name).read_text()
        assert "import_tasks: tasks/disable-forwarder.yml" in source
        assert "systemctl, disable, --now" not in source


def test_delegate_diagnostic_is_explicitly_profile_aware():
    wrapper = (ROOT / "deploy.sh").read_text()
    diagnostics = DIAGNOSE.read_text()
    assert "ocpp_csms_diagnose_delegate=true" in wrapper
    assert "when: not (ocpp_csms_diagnose_delegate | default(false) | bool)" in diagnostics
    assert "delegate deployment does not install Discover" in diagnostics
    assert "safety_preflight_passed:" in diagnostics
    assert "incumbent_observation_succeeded:" in diagnostics
