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
    create = task_by_name(TASKS / "main.yml", "Create disabled default mail configuration if absent")
    settings = create["ansible.builtin.template"]
    assert settings["force"] is False
    assert settings["owner"] == "root"
    assert settings["mode"] == "0640"
    assert settings["src"] == "mail.toml.j2"
    check = task_by_name(TASKS / "main.yml", "Validate existing mail configuration as service user")
    assert check["changed_when"] is False
    assert "load_mail_config" in str(check["ansible.builtin.command"])
    assert "policy" in str(check["ansible.builtin.command"])
    assert "_jobs" in str(check["ansible.builtin.command"])


def test_timer_is_explicitly_opt_in():
    defaults = load_yaml(ROOT / "ansible" / "roles" / "ocpp_csms" / "defaults" / "main.yml")
    assert defaults["ocpp_csms_mail_timer_enabled"] is False
    timer = task_by_name(TASKS / "main.yml", "Configure scheduled report timer enablement")
    options = timer["ansible.builtin.systemd_service"]
    assert "ocpp_csms_mail_timer_enabled" in options["enabled"]
    assert "stopped" in options["state"]
