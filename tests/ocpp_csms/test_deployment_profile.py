"""Safe deploy-profile lifecycle tested through the real deploy shell entry point."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy.sh"


def appliance(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_id = bin_dir / "id"
    fake_id.write_text("#!/bin/sh\nprintf '1000\\n'\n")
    fake_id.chmod(0o755)
    fake_ansible = bin_dir / "ansible-playbook"
    fake_ansible.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$FAKE_ANSIBLE_LOG\"\n"
        "exit \"${FAKE_ANSIBLE_EXIT:-0}\"\n"
    )
    fake_ansible.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "HOME": str(tmp_path),
        "OCPP_CSMS_DEPLOY_PROFILE_PATH": str(tmp_path / "state" / "deployment.json"),
        "FAKE_ANSIBLE_LOG": str(tmp_path / "ansible.log"),
    })
    def invoke(*args, failed=False):
        current = dict(env)
        if failed:
            current["FAKE_ANSIBLE_EXIT"] = "1"
        return subprocess.run(["sh", str(SCRIPT), *args], cwd=ROOT, env=current, capture_output=True, text=True)
    return invoke, Path(env["OCPP_CSMS_DEPLOY_PROFILE_PATH"]), tmp_path / "ansible.log"


def test_delegate_and_exposed_profile_survive_flagless_redeploy(tmp_path, monkeypatch):
    run, profile, log = appliance(tmp_path, monkeypatch)
    assert run("--delegate").returncode == 0
    assert json.loads(profile.read_text())["mode"] == "delegate"
    assert run().returncode == 0
    assert "delegate.yml" in log.read_text().splitlines()[-1]

    assert run("--expose", "porsche-abb.gelectriic.com", "--email", "ops@example.com",
               "--forwarder").returncode == 0
    settings = json.loads(profile.read_text())
    assert settings["expose"] == "porsche-abb.gelectriic.com"
    assert settings["email"] == "ops@example.com"
    assert settings["forwarder"] is True
    assert profile.stat().st_mode & 0o777 == 0o600
    assert run().returncode == 0
    last = log.read_text().splitlines()[-1]
    assert "delegate.yml" in last
    assert "ocpp_csms_expose=porsche-abb.gelectriic.com" in last
    assert "ocpp_forwarder_enabled=true" in last
    assert "ocpp_csms_certbot_email=ops@example.com" in last


def test_mode_switch_and_expose_change_require_uninstall(tmp_path, monkeypatch):
    run, profile, log = appliance(tmp_path, monkeypatch)
    assert run("--delegate").returncode == 0
    before = profile.read_text()
    assert run("--satellite").returncode != 0
    assert profile.read_text() == before
    assert run("--expose", "first.example.com", "--email", "ops@example.com").returncode == 0
    assert run("--expose", "second.example.com").returncode != 0


def test_recovery_diagnostics_and_stage_only_do_not_overwrite_profile(tmp_path, monkeypatch):
    run, profile, log = appliance(tmp_path, monkeypatch)
    assert run("--expose", "porsche-abb.gelectriic.com", "--email", "ops@example.com").returncode == 0
    before = profile.read_text()
    assert run("--diagnose").returncode == 0
    assert "diagnose.yml" in log.read_text().splitlines()[-1]
    assert profile.read_text() == before
    assert run("--recover").returncode == 0
    assert profile.read_text() == before
    assert run("--stage-only").returncode == 0
    assert profile.read_text() == before
    assert run("--observe", "5").returncode == 0
    assert profile.read_text() == before


def test_failed_deploy_preserves_profile_and_uninstall_clears_after_success(tmp_path, monkeypatch):
    run, profile, log = appliance(tmp_path, monkeypatch)
    assert run("--delegate").returncode == 0
    before = profile.read_text()
    assert run("--forwarder", failed=True).returncode != 0
    assert profile.read_text() == before
    assert run("--uninstall", failed=True).returncode != 0
    assert profile.read_text() == before
    assert run("--uninstall").returncode == 0
    assert not profile.exists()
    assert "uninstall.yml" in log.read_text().splitlines()[-1]
    assert run("--satellite").returncode == 0
    assert json.loads(profile.read_text())["mode"] == "satellite"


def test_forwarder_can_be_explicitly_disabled_and_stays_disabled(tmp_path, monkeypatch):
    run, profile, log = appliance(tmp_path, monkeypatch)
    assert run("--satellite", "--forwarder").returncode == 0
    assert run("--no-forwarder").returncode == 0
    assert json.loads(profile.read_text())["forwarder"] is False
    assert run().returncode == 0
    assert "ocpp_forwarder_enabled=true" not in log.read_text().splitlines()[-1]
