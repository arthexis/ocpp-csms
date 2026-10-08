from .helpers import ANSIBLE, DEPLOY_SCRIPT, load_yaml, read, task_by_name


PLAYBOOK = ANSIBLE / "playbooks" / "satellite.yml"
ROLE = ANSIBLE / "roles" / "ocpp_forwarder"
DEFAULTS = ROLE / "defaults" / "main.yml"
TASKS = ROLE / "tasks" / "main.yml"
SERVICE = ROLE / "templates" / "ocpp-forwarder.service.j2"
ENVIRONMENT = ROLE / "templates" / "environment.j2"


def test_forwarder_role_is_opt_in_in_satellite_composition():
    plays = load_yaml(PLAYBOOK)
    roles = plays[0]["roles"]
    forwarder = roles[-1]

    assert forwarder["role"] == "ocpp_forwarder"
    assert forwarder["when"] == "ocpp_forwarder_enabled | default(false) | bool"


def test_forwarder_defaults_to_disabled():
    defaults = load_yaml(DEFAULTS)

    assert defaults["ocpp_forwarder_enabled"] is False
    assert defaults["ocpp_forwarder_collector_url"] == "https://ocpp-collector.arthexis.com"


def test_deploy_wrapper_enables_forwarder_only_with_explicit_flag():
    wrapper = read(DEPLOY_SCRIPT)

    assert "--forwarder)" in wrapper
    assert "ocpp_forwarder_enabled=true" in wrapper


def test_forwarder_uses_active_immutable_release_command():
    defaults = load_yaml(DEFAULTS)

    assert defaults["ocpp_forwarder_command"] == (
        "{{ ocpp_csms_current_link }}/venv/bin/ocpp-forwarder"
    )
    assert defaults["ocpp_forwarder_csms_command"] == "{{ ocpp_csms_command_path }}"


def test_forwarder_token_is_root_configured_and_mode_0600():
    task = task_by_name(TASKS, "Install OCPP Forwarder token")
    spec = task["ansible.builtin.copy"]

    assert spec["dest"] == "{{ ocpp_forwarder_token_path }}"
    assert spec["mode"] == "0600"
    assert task["no_log"] is True


def test_forwarder_state_is_separate_from_csms_storage():
    defaults = load_yaml(DEFAULTS)

    assert defaults["ocpp_forwarder_state_dir"] == "/var/lib/ocpp-forwarder"
    assert "ocpp-csms-data" not in defaults["ocpp_forwarder_state_dir"]


def test_forwarder_service_has_no_csms_dependency_edges():
    service = read(SERVICE)

    assert "Requires=ocpp-csms.service" not in service
    assert "PartOf=ocpp-csms.service" not in service
    assert "BindsTo=ocpp-csms.service" not in service
    assert "After=ocpp-csms.service" not in service
    assert "Restart=always" in service
    assert "RestartSec=5" in service


def test_csms_service_does_not_reference_forwarder():
    csms_service = read(
        ANSIBLE / "roles" / "ocpp_csms" / "templates" / "ocpp-csms.service.j2"
    )

    assert "forwarder" not in csms_service.lower()


def test_forwarder_environment_keeps_token_out_of_environment():
    environment = read(ENVIRONMENT)

    assert "OCPP_FORWARDER_TOKEN_FILE=" in environment
    assert "OCPP_FORWARDER_TOKEN=" not in environment


def test_forwarder_deployment_never_probes_collector_reachability():
    tasks = read(TASKS)

    assert "ansible.builtin.uri:" not in tasks
    assert "ocpp_collector" not in tasks
    assert "Confirm OCPP Forwarder service is active" in tasks


def test_configuration_changes_restart_only_forwarder():
    restart = task_by_name(TASKS, "Restart OCPP Forwarder when configuration changes")

    assert restart["ansible.builtin.systemd_service"]["name"] == (
        "{{ ocpp_forwarder_service_name }}"
    )
    assert restart["ansible.builtin.systemd_service"]["state"] == "restarted"


def test_forwarder_requires_https_and_local_prerequisites():
    validation = task_by_name(TASKS, "Validate OCPP Forwarder configuration")
    conditions = validation["ansible.builtin.assert"]["that"]

    assert "ocpp_forwarder_collector_url.startswith('https://')" in conditions
    assert "ocpp_forwarder_token | length > 0" in conditions
