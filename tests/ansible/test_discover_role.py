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
    assert 'ocpp_discover_runtime_command: "{{ ocpp_discover_prefix }}/current/venv/bin/ocpp-discover"' in defaults
    assert "{{ ocpp_discover_python }}" in template
    assert "/venv/bin/python" not in template.replace("{{ ocpp_discover_python }}", "")


def test_discover_exposes_stable_global_operator_command():
    defaults = read(DISCOVER_DEFAULTS)
    tasks = read(DISCOVER_TASKS)
    assert "ocpp_discover_command_path: /usr/local/bin/ocpp-discover" in defaults
    assert_task_order(
        tasks,
        "Verify active immutable OCPP Discover command exists",
        "Require active immutable OCPP Discover command",
        "Install stable OCPP Discover command",
        "Resolve stable OCPP Discover command target",
        "Resolve active OCPP Discover release command",
        "Assert stable OCPP Discover command target",
        "Install OCPP Discover runtime packages",
    )
    install = task_section(tasks, "Install stable OCPP Discover command")
    assert 'src: "{{ ocpp_discover_runtime_command }}"' in install
    assert 'dest: "{{ ocpp_discover_command_path }}"' in install
    assert "state: link" in install


def test_discover_is_resident_restartable_service():
    template = read(DISCOVER_TEMPLATE)
    assert "Type=simple" in template
    assert "Type=oneshot" not in template
    assert "RemainAfterExit" not in template
    assert "Restart=on-failure" in template
    assert "RestartSec=5" in template


def test_discover_validates_interface_runtime_and_shared_data_before_mutation():
    tasks = read(DISCOVER_TASKS)
    assert_task_order(
        tasks,
        "Inspect OCPP Discover charger-facing interface",
        "Require OCPP Discover charger-facing interface",
        "Verify active immutable CSMS Python runtime exists",
        "Require active immutable CSMS Python runtime",
        "Verify active immutable OCPP Discover command exists",
        "Require active immutable OCPP Discover command",
        "Inspect shared OCPP CSMS data directory",
        "Require shared OCPP CSMS data directory",
        "Install OCPP Discover runtime packages",
    )

    interface = task_section(tasks, "Inspect OCPP Discover charger-facing interface")
    assert "/sys/class/net/{{ ocpp_discover_interface }}" in interface


def test_discover_installs_static_host_integration_before_service_activation():
    tasks = read(DISCOVER_TASKS)
    assert_task_order(
        tasks,
        "Install OCPP Discover runtime packages",
        "Ensure OCPP Discover persistent state directory exists",
        "Prepare OCPP Discover persistent integration",
        "Validate OCPP Discover persistent integration result",
        "Enable nftables service for future boots",
        "Render candidate OCPP Discover systemd unit",
        "Verify candidate OCPP Discover systemd unit",
        "Install live OCPP Discover systemd unit",
        "Enable OCPP Discover at boot",
        "Restart OCPP Discover when its effective unit changes",
        "Ensure unchanged OCPP Discover service is running",
        "Confirm OCPP Discover service is enabled",
        "Confirm OCPP Discover service is active",
    )


def test_discover_persistent_directory_is_static_root_owned_state():
    section = task_section(
        read(DISCOVER_TASKS), "Ensure OCPP Discover persistent state directory exists"
    )
    assert 'path: "{{ ocpp_discover_persistent_dir }}"' in section
    assert "owner: root" in section
    assert "group: root" in section
    assert 'mode: "0755"' in section


def test_discover_lifecycle_reports_real_ansible_change_state():
    section = task_section(
        read(DISCOVER_TASKS), "Prepare OCPP Discover persistent integration"
    )
    assert "--persistent-dir" in section
    assert "--json" in section
    assert "register: ocpp_discover_prepare" in section
    assert "from_json" in section
    assert ".changed | bool" in section
    assert "changed_when: false" not in section


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


def test_discover_unit_change_is_the_only_explicit_restart_trigger():
    tasks = read(DISCOVER_TASKS)
    restart = task_section(tasks, "Restart OCPP Discover when its effective unit changes")
    assert "state: restarted" in restart
    assert "when: ocpp_discover_unit_install.changed" in restart

    assert tasks.count("state: restarted") == 1


def test_unchanged_discover_convergence_does_not_restart_service():
    tasks = read(DISCOVER_TASKS)
    started = task_section(tasks, "Ensure unchanged OCPP Discover service is running")
    assert "state: started" in started
    assert "when: not ocpp_discover_unit_install.changed" in started
    assert "restarted" not in started


def test_discover_service_is_enabled_and_required_active():
    tasks = read(DISCOVER_TASKS)
    enabled = task_section(tasks, "Enable OCPP Discover at boot")
    active = task_section(tasks, "Confirm OCPP Discover service is active")

    assert "enabled: true" in enabled
    assert "is-active" in active
    assert "failed_when: ocpp_discover_active.stdout.strip() != 'active'" in active
