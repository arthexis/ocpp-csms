"""Prevent reintroduction of the retired Gway-001 self-hosted CI pathway."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def test_gway001_runner_and_appliance_dispatch_are_retired():
    assert not (WORKFLOWS / "appliance.yml").exists()
    ci = (WORKFLOWS / "ci.yml").read_text()
    assert "self-hosted" not in ci
    assert "gway-001" not in ci.lower()
    assert "SIMULATOR_LABEL" not in ci
    assert "SIMULATOR_RESULT" not in ci
    assert "TRUSTED_SIMULATOR_PR" not in ci
    assert "simulator-pr-" not in ci


def test_manual_deploy_routes_to_gway_remote():
    deploy = (WORKFLOWS / "deploy-gway-001.yml").read_text()
    assert "workflow_dispatch:" in deploy
    assert "runs-on: ubuntu-latest" in deploy
    assert "repos/arthexis/gway-remote/dispatches" in deploy
    assert "event_type=ocpp-csms-deploy" in deploy
    assert "client_payload[sha]" in deploy
    assert "self-hosted" not in deploy
