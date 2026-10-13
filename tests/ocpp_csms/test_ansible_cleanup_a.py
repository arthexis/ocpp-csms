"""Regression checks for profile-aware diagnostics."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIAGNOSE = ROOT / "ansible/roles/ocpp_csms/tasks/diagnose.yml"


def test_delegate_diagnostic_is_explicitly_profile_aware():
    wrapper = (ROOT / "deploy.sh").read_text()
    diagnostics = DIAGNOSE.read_text()
    assert "ocpp_csms_diagnose_delegate=true" in wrapper
    assert "when: not (ocpp_csms_diagnose_delegate | default(false) | bool)" in diagnostics
    assert "delegate deployment does not install Discover" in diagnostics
    assert "safety_preflight_passed:" in diagnostics
    assert "incumbent_observation_succeeded:" in diagnostics
