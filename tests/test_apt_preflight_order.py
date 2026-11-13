"""Guard package preflight ordering, retry policy, and cutover boundaries."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_appliance_apt_preflight_precedes_csms_cutover():
    playbook = (ROOT / "ansible/playbooks/install.yml").read_text()
    assert playbook.index("Preflight all appliance runtime packages") < playbook.index("Deploy OCPP CSMS")
    assert playbook.index("Deploy OCPP CSMS") < playbook.index("Deploy OCPP Discover")
    for required in ("cache_valid_time: 3600", "retries: 3", "delay: 5", "nftables", "update_cache: true"):
        assert required in playbook


def test_roles_do_not_install_packages_after_cutover():
    for role in ("ocpp_csms", "ocpp_discover"):
        tasks = (ROOT / f"ansible/roles/{role}/tasks/main.yml").read_text()
        assert "ansible.builtin.apt:" not in tasks
        assert "update_cache: true" not in tasks
