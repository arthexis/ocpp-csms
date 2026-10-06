from .helpers import (
    ANSIBLE,
    PLAYBOOK,
    assert_named_task_order,
    load_yaml,
    read,
    task_by_name,
    task_names,
)


DISCOVER_ROLE = ANSIBLE / "roles" / "ocpp_discover"
DISCOVER_DEFAULTS = DISCOVER_ROLE / "defaults" / "main.yml"
DISCOVER_TASKS = DISCOVER_ROLE / "tasks" / "main.yml"
DISCOVER_TEMPLATE = DISCOVER_ROLE / "templates" / "ocpp-discover.service.j2"


def test_satellite_converges_csms_before_discover():
    plays = load_yaml(PLAYBOOK)
    roles = plays[0]["roles"]
    names = [role["role"] if isinstance(role, dict) else role for role in roles]
    assert names.index("ocpp_csms") < names.index("ocpp_discover")


def test_discover_uses_active_immutable_csms_runtime():
    defaults = load_yaml(DISCOVER_DEFAULTS)
    template = read(DISCOVER_TEMPLATE)

    assert defaults["ocpp_discover_python"].endswith("/current/venv/bin/python")
    assert defaults["ocpp_discover_runtime_command"].endswith("/current/venv/bin/ocpp-discover")
    assert "{{ ocpp_discover_python }}" in template


def test_discover_exposes_stable_global_operator_command():
    defaults = load_yaml(DISCOVER_DEFAULTS)
    install = task_by_name(DISCOVER_TASKS, "Install stable OCPP Discover command")["ansible.builtin.file"]

    assert defaults["ocpp_discover_command_path"] == "/usr/local/bin/ocpp-discover"
    assert install["src"] == "{{ ocpp_discover_runtime_command }}"
    assert install["dest"] == "{{ ocpp_discover_command_path }}"
    assert install["state"] == "link"


def test_discover_is_resident_restartable_service():
    template = read(DISCOVER_TEMPLATE)
    assert "Type=simple" in template
    assert "Restart=on-failure" in template
    assert "RestartSec=5" in template


def test_discover_validates_host_inputs_before_installing_runtime_packages():
    assert_named_task_order(
        DISCOVER_TASKS,
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


def test_discover_installs_static_integration_before_service_activation():
    assert_named_task_order(
        DISCOVER_TASKS,
        "Install OCPP Discover runtime packages",
        "Ensure OCPP Discover persistent state directory exists",
        "Prepare OCPP Discover persistent integration",
        "Validate OCPP Discover persistent integration result",
        "Enable nftables service for future boots",
        "Render candidate OCPP Discover systemd unit",
        "Verify candidate OCPP Discover systemd unit",
        "Install live OCPP Discover systemd unit",
        "Enable OCPP Discover at boot",
        "Confirm OCPP Discover service is active",
    )


def test_discover_persistent_directory_is_root_owned():
    spec = task_by_name(
        DISCOVER_TASKS, "Ensure OCPP Discover persistent state directory exists"
    )["ansible.builtin.file"]
    assert spec["path"] == "{{ ocpp_discover_persistent_dir }}"
    assert spec["owner"] == "root"
    assert spec["group"] == "root"


def test_discover_lifecycle_reports_ansible_change_state():
    task = task_by_name(DISCOVER_TASKS, "Prepare OCPP Discover persistent integration")
    argv = task["ansible.builtin.command"]["argv"]
    assert argv[:4] == [
        "{{ ocpp_discover_python }}",
        "-m",
        "ocpp_discover.lifecycle",
        "prepare",
    ]
    assert task["register"] == "ocpp_discover_prepare"


def test_nftables_is_enabled_for_future_boots():
    spec = task_by_name(
        DISCOVER_TASKS, "Enable nftables service for future boots"
    )["ansible.builtin.systemd_service"]
    assert spec["enabled"] is True


def test_discover_unit_change_is_the_only_explicit_restart_trigger():
    restart = task_by_name(DISCOVER_TASKS, "Restart OCPP Discover when its effective unit changes")
    assert restart["ansible.builtin.systemd_service"]["state"] == "restarted"
    assert restart["when"] == "ocpp_discover_unit_install.changed"

    restart_tasks = []
    for name in task_names(DISCOVER_TASKS):
        task = task_by_name(DISCOVER_TASKS, name)
        spec = task.get("ansible.builtin.systemd_service", {})
        if spec.get("state") == "restarted":
            restart_tasks.append(name)
    assert restart_tasks == ["Restart OCPP Discover when its effective unit changes"]


def test_unchanged_discover_convergence_keeps_service_started():
    started = task_by_name(DISCOVER_TASKS, "Ensure unchanged OCPP Discover service is running")
    assert started["ansible.builtin.systemd_service"]["state"] == "started"
    assert started["when"] == "not ocpp_discover_unit_install.changed"


def test_discover_service_is_enabled_and_checked_active():
    enabled = task_by_name(DISCOVER_TASKS, "Enable OCPP Discover at boot")
    active = task_by_name(DISCOVER_TASKS, "Confirm OCPP Discover service is active")

    assert enabled["ansible.builtin.systemd_service"]["enabled"] is True
    assert active["ansible.builtin.command"]["argv"][:2] == ["systemctl", "is-active"]
