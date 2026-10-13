"""Mail configuration provisioning remains safe on repeated deployments."""
import tomllib

from .helpers import ROOT, TASKS, load_yaml, read, task_by_name


def test_default_mail_file_is_parseable_and_fully_disabled():
    template = ROOT / "ansible" / "roles" / "ocpp_csms" / "templates" / "mail.toml.j2"
    data = tomllib.loads(read(template))
    mail = data["mail"]
    assert mail["enabled"] is False
    assert mail["from"] == ""
    assert mail["to"] == []
    assert mail["alerts"]["enabled"] is False
    assert mail["events"]["transaction_started"]["enabled"] is False
    assert mail["events"]["transaction_stopped"]["enabled"] is False
    assert mail["reports"]["daily"]["enabled"] is False
    assert mail["reports"]["weekly"]["enabled"] is False
    assert mail["reports"]["daily"]["timezone"] == "UTC"
    assert mail["smtp"]["password_env"] == "OCPP_CSMS_SMTP_PASSWORD"


def test_deployment_creates_once_preserves_edits_and_validates():
    main = read(TASKS / "main.yml")
    assert "import_tasks: mail.yml" in main
    assert "Create OCPP CSMS mail configuration directory" not in main
    assert main.index("Prepare persistent OCPP CSMS TLS directories") < main.index("Maintain OCPP CSMS mail settings and report timer")
    create = task_by_name(TASKS / "mail.yml", "Create disabled default mail configuration if absent")
    settings = create["ansible.builtin.template"]
    assert settings["force"] is False
    assert settings["owner"] == "{{ ocpp_csms_service_user }}"
    assert settings["mode"] == "0640"
    assert settings["src"] == "mail.toml.j2"
    directory = task_by_name(TASKS / "main.yml", "Prepare persistent OCPP CSMS TLS directories")
    assert any(item["path"] == "/etc/ocpp-csms" and item["mode"] == "0750" for item in directory["loop"])
    preserve = task_by_name(TASKS / "mail.yml", "Preserve existing mail configuration with deploy-user ownership")
    persistent = preserve["ansible.builtin.file"]
    assert persistent["state"] == "file"
    assert persistent["owner"] == "{{ ocpp_csms_service_user }}"
    assert persistent["group"] == "{{ ocpp_csms_service_group }}"
    assert persistent["mode"] == "0640"
    assert "content" not in persistent

    check = task_by_name(TASKS / "mail.yml", "Validate existing mail configuration as service user")
    assert check["changed_when"] is False
    assert "load_mail_config" in str(check["ansible.builtin.command"])
    assert "policy" in str(check["ansible.builtin.command"])
    assert "_jobs" in str(check["ansible.builtin.command"])


def test_timer_is_explicitly_opt_in():
    defaults = load_yaml(ROOT / "ansible" / "roles" / "ocpp_csms" / "defaults" / "main.yml")
    assert defaults["ocpp_csms_mail_timer_enabled"] is False
    timer = task_by_name(TASKS / "mail.yml", "Configure scheduled report timer enablement")
    options = timer["ansible.builtin.systemd_service"]
    assert "ocpp_csms_mail_timer_enabled" in options["enabled"]
    assert "stopped" in options["state"]
