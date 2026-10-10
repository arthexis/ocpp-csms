"""Architectural boundary: deployment never creates ad-hoc services.

Discover is responsible for its own reconnection lifecycle. CSMS cutover may
verify connectivity and roll back, but must not manage Discover services.
"""
from pathlib import Path

from .helpers import TASKS, load_yaml, read


def test_reconnect_verifies_without_starting_discover():
    tasks = load_yaml(TASKS / "reconnect.yml")
    assert len(tasks) == 2
    assert {t["name"] for t in tasks} == {
        "Verify managed charger reconnect",
        "Verify takeover charger reconnect",
    }
    for task in tasks:
        cmd = task["ansible.builtin.command"]["argv"]
        assert "ocpp_csms.install_cutover" in cmd
        assert "{{ ocpp_csms_reconnect_timeout }}" in cmd
        assert task["changed_when"] is False


def test_no_transient_services_or_discover_lifecycle_inside_csms_ansible():
    role = TASKS.parent
    for path in list((role / "tasks").glob("*.yml")) + list((role / "templates").glob("*")):
        data = read(path)
        assert "systemd-run" not in data, f"unexpected transient unit in {path}"
        assert "ocpp-discover-handoff" not in data, f"unexpected handoff unit in {path}"
    reconnect = read(TASKS / "reconnect.yml")
    assert "systemctl" not in reconnect
    assert "ocpp_discover" not in reconnect
    tasks = load_yaml(TASKS / "reconnect.yml")
    for task in tasks:
        assert "ansible.builtin.systemd_service" not in task
        assert "ansible.builtin.service" not in task
        argv = task["ansible.builtin.command"]["argv"]
        assert not any(token in argv for token in ("systemd-run", "systemctl", "service"))


def test_systemd_units_are_explicitly_allowlisted():
    templates = TASKS.parent / "templates"
    units = {p.name for p in templates.glob("*.service.j2")} | {
        p.name for p in templates.glob("*.timer.j2")
    }
    assert units == {
        "ocpp-csms.service.j2",
        "ocpp-csms-mail-report.service.j2",
        "ocpp-csms-mail-report.timer.j2",
    }, "New services or timers require deliberate architecture review"
