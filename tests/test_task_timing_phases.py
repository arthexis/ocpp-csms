"""Exercise passive phase instrumentation without requiring Ansible on pytest hosts."""
from __future__ import annotations

import runpy
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

CALLBACK = (
    Path(__file__).resolve().parents[1]
    / "ansible/callback_plugins/task_timing.py"
)


def test_handoff_phase_reporting_includes_systemd_handler_without_extra_tasks(monkeypatch):
    output = []

    class StubBase:
        def __init__(self):
            self._display = SimpleNamespace(display=output.append)

    ansible = ModuleType("ansible")
    plugins = ModuleType("ansible.plugins")
    callback = ModuleType("ansible.plugins.callback")
    callback.CallbackBase = StubBase
    monkeypatch.setitem(sys.modules, "ansible", ansible)
    monkeypatch.setitem(sys.modules, "ansible.plugins", plugins)
    monkeypatch.setitem(sys.modules, "ansible.plugins.callback", callback)
    callback_cls = runpy.run_path(str(CALLBACK))["CallbackModule"]

    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    plugin = callback_cls()

    def finished(name, start, elapsed, *, handler=False, skipped=False):
        task = SimpleNamespace(_uuid=name, get_name=lambda: "ocpp_csms : " + name)
        clock[0] = start
        if handler:
            plugin.v2_playbook_on_handler_task_start(task)
        else:
            plugin.v2_playbook_on_task_start(task, False)
        clock[0] += elapsed
        plugin.v2_runner_on_ok(
            SimpleNamespace(_task=task, _result={"skipped": skipped})
        )

    finished("Start critical handoff timer", 0.0, 0.1)
    finished("Stop existing OCPP CSMS service", 1.0, 0.4)
    finished("Verify selected storage before service activation", 2.0, 0.2)
    finished("Install live OCPP CSMS systemd unit", 3.0, 0.3)
    finished("Reload systemd daemon", 4.0, 0.8, handler=True)
    finished("Verify OCPP CSMS storage access as systemd service user", 5.0, 0.3)
    finished("Enable and start candidate OCPP CSMS service", 6.0, 0.6)
    finished("Wait for OCPP CSMS listener", 7.0, 0.4)
    finished("Verify candidate OCPP CSMS application status", 8.0, 0.2)

    assert "TIMING HANDOFF PHASE [stop] 0.40s" in output
    assert "TIMING HANDOFF PHASE [storage] 0.50s" in output
    assert "TIMING HANDOFF PHASE [systemd] 1.10s" in output
    assert "TIMING HANDOFF PHASE [startup] 0.60s" in output
    assert "TIMING HANDOFF PHASE [health] 0.60s" in output
    assert "TIMING [ocpp_csms : Reload systemd daemon] 0.80s" in output


def test_handoff_phases_ignore_skipped_stops_and_reset_between_attempts(monkeypatch):
    lines = []

    class StubBase:
        def __init__(self):
            self._display = SimpleNamespace(display=lines.append)

    ansible = ModuleType("ansible")
    plugins = ModuleType("ansible.plugins")
    callback = ModuleType("ansible.plugins.callback")
    callback.CallbackBase = StubBase
    monkeypatch.setitem(sys.modules, "ansible", ansible)
    monkeypatch.setitem(sys.modules, "ansible.plugins", plugins)
    monkeypatch.setitem(sys.modules, "ansible.plugins.callback", callback)
    instance = runpy.run_path(str(CALLBACK))["CallbackModule"]()
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def step(name, skipped=False):
        task = SimpleNamespace(_uuid=f"{name}:{len(lines)}", get_name=lambda: "ocpp_csms : " + name)
        instance.v2_playbook_on_task_start(task, False)
        clock[0] += 0.1
        instance.v2_runner_on_ok(SimpleNamespace(_task=task, _result={"skipped": skipped}))

    step("Start critical handoff timer")
    step("Stop existing OCPP CSMS service", skipped=True)
    step("Verify candidate OCPP CSMS application status")
    assert not any("PHASE [stop]" in line for line in lines)
    step("Start critical handoff timer")
    step("Stop existing OCPP CSMS service")
    step("Verify candidate OCPP CSMS application status")
    assert sum("PHASE [stop]" in line for line in lines) == 1
