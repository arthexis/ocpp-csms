from pathlib import Path

from .helpers import ANSIBLE, PLAYBOOK, assert_task_order, read, task_section


DISCOVER_ROLE = ANSIBLE / "roles" / "ocpp_discover"
DISCOVER_DEFAULTS = DISCOVER_ROLE / "defaults" / "main.yml"
DISCOVER_TASKS = DISCOVER_ROLE / "tasks" / "main.yml"
DISCOVER_TEMPLATE = DISCOVER_ROLE / "templates" / "ocpp-discover.service.j2"


def test_satellite_converges_csms_before_discover():
    playbook = read(PLAYBOOK)
    assert playbook.index("- role: ocpp_csms") < playbook.index("- role: ocpp_discover")


def test_discover_uses_active_immutable_csms_runtime():
    defaults = read(DISCOVER_DEFAULTS)
    template = read(DISCOVER_TEMPLATE)

    assert 'ocpp_discover_python: "{{ ocpp_discover_prefix }}/current/venv/bin/python"' in defaults
    assert "{{ ocpp_discover_python }}" in template
    assert "/venv/bin/python" not in template.replace("{{ ocpp_discover_python }}", "")


def test_discover_installs_static_host_integration_before_unit_enablement():
    tasks = read(DISCOVER_TASKS)
    assert_task_order(
        tasks,
        "Install OCPP Discover runtime packages",
        "Prepare OCPP Discover persistent integration",
        "Enable nftables service for future boots",
        "Render candidate OCPP Discover systemd unit",
        "Verify candidate OCPP Discover systemd unit",
        "Install live OCPP Discover systemd unit",
        "Enable OCPP Discover at boot",
    )


def test_nftables_is_enabled_without_runtime_restart_or_flush():
    tasks = read(DISCOVER_TASKS)
    section = task_section(tasks, "Enable nftables service for future boots")

    assert "enabled: true" in section
    assert "state:" not in section
    assert "restart" not in section.lower()
    assert "flush" not in section.lower()


def test_discover_role_delegates_network_reasoning_to_python():
    tasks = read(DISCOVER_TASKS)

    prepare = task_section(tasks, "Prepare OCPP Discover persistent integration")
    assert "ocpp_discover.lifecycle" in prepare
    assert "prepare" in prepare

    forbidden = ("nft add", "nft delete", "ip addr add", "tcpdump")
    for command in forbidden:
        assert command not in tasks.lower()


def test_discover_service_is_enabled_but_not_forced_to_run_during_convergence():
    tasks = read(DISCOVER_TASKS)
    section = task_section(tasks, "Enable OCPP Discover at boot")

    assert "enabled: true" in section
    assert "state:" not in section
