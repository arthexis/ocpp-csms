"""Deployment-path contracts for Ansible refactors.

These are static regressions: they never operate on appliances.
"""
from .helpers import ROOT, read, load_yaml

PLAYBOOKS = ROOT / "ansible/playbooks"


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
