"""Deployment selection contract: Discover is conditional on eth0, CSMS is not."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = ROOT / "ansible/playbooks/install.yml"


def _task(name):
    # Match a complete playbook task block without requiring Ansible at test runtime.
    source = PLAYBOOK.read_text()
    marker = f"    - name: {name}\n"
    assert source.count(marker) == 1
    return source.split(marker, 1)[1].split("\n    - name:", 1)[0]


def test_deploy_detects_eth0_without_charger_activity_probe():
    detect = _task("Detect eth0 interface")
    assert "ansible.builtin.stat:" in detect
    assert "path: /sys/class/net/eth0" in detect
    assert "register: ocpp_eth0" in detect
    assert "when:" not in detect


def test_csms_role_is_unconditional():
    task = _task("Deploy OCPP CSMS")
    assert "ansible.builtin.include_role:" in task
    assert "name: ocpp_csms" in task
    assert "when:" not in task


def test_discover_role_requires_eth0_and_uses_same_interface():
    task = _task("Deploy OCPP Discover when eth0 is available")
    assert "ansible.builtin.include_role:" in task
    assert "name: ocpp_discover" in task
    assert "ocpp_discover_interface: eth0" in task
    assert "when: ocpp_eth0.stat.exists" in task


def test_discover_selection_does_not_require_connected_charger():
    source = PLAYBOOK.read_text()
    assert "install_preflight" not in source
    assert "connected_chargers" not in source
    assert "active_chargers" not in source
    assert "ocpp_eth0.stat.exists" in source
