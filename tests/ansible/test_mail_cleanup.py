"""Retiring mail delivery must not remove charging evidence or notification history."""
from .helpers import TASKS, load_yaml, read


def test_mail_cleanup_is_part_of_deployment():
    main = read(TASKS / "main.yml")
    assert "import_tasks: mail_cleanup.yml" in main
    assert "import_tasks: mail.yml" not in main


def test_mail_units_are_stopped_disabled_and_removed():
    tasks = load_yaml(TASKS / "mail_cleanup.yml")
    text = str(tasks)
    assert "ocpp-csms-mail-report.timer" in text
    assert "ocpp-csms-mail-report.service" in text
    assert "state': 'stopped'" in text
    assert "enabled': False" in text
    assert "daemon_reload': True" in text
    assert "notifications.sqlite3" not in text
    assert "events.sqlite3" not in text
