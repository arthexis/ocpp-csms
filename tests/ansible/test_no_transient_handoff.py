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
    assert "service" not in reconnect.lower().replace("verification", "")
