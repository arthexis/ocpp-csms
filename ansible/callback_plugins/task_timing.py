from __future__ import annotations

import time

from ansible.plugins.callback import CallbackBase


DOCUMENTATION = r"""
name: task_timing
type: aggregate
short_description: Report elapsed time for each Ansible task
description:
  - Records task start times and prints elapsed wall-clock time when each task finishes.
"""


# Passive timing: no additional Ansible commands or round trips in the
# charger-facing interruption window. These times sum Ansible task execution
# (including systemd handler execution), not true network socket downtime.
_HANDOFF_PHASES = {
    "Stop existing OCPP CSMS service": "stop",
    "Verify prepared storage handoff contract after service stop": "storage",
    "Verify selected storage before service activation": "storage",
    "Verify OCPP CSMS storage access as systemd service user": "storage",
    "Mark candidate writer as potentially started before service start": "storage",
    "Install live OCPP CSMS systemd unit": "systemd",
    "Reload systemd daemon": "systemd",
    "Apply pending systemd daemon reload": "systemd",
    "Enable and start candidate OCPP CSMS service": "startup",
    "Wait for OCPP CSMS listener": "health",
    "Verify candidate OCPP CSMS application status": "health",
}
_HANDOFF_ORDER = ("stop", "storage", "systemd", "startup", "health")


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "task_timing"
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self):
        super().__init__()
        self._started: dict[str, float] = {}
        self._handoff_active = False
        self._handoff_phases: dict[str, float] = {}

    def v2_playbook_on_task_start(self, task, is_conditional):
        self._started[task._uuid] = time.monotonic()

    def v2_playbook_on_handler_task_start(self, task):
        # Include daemon-reload handlers, otherwise the largest systemd
        # cost in the cutover would be invisible to the phase report.
        self._started[task._uuid] = time.monotonic()

    def _report(self, result):
        task = result._task
        started = self._started.pop(task._uuid, None)
        if started is None:
            return
        elapsed = time.monotonic() - started
        full_name = task.get_name()
        self._display.display(f"TIMING [{full_name}] {elapsed:.2f}s")

        # Keep the original per-task lines intact for existing consumers.
        name = full_name.split(" : ", 1)[-1]
        if name == "Start critical handoff timer":
            self._handoff_active = True
            self._handoff_phases.clear()
            return
        if not self._handoff_active or result._result.get("skipped"):
            return
        phase = _HANDOFF_PHASES.get(name)
        if phase:
            self._handoff_phases[phase] = self._handoff_phases.get(phase, 0.0) + elapsed
        if name == "Verify candidate OCPP CSMS application status" and not result._result.get("failed"):
            for group in _HANDOFF_ORDER:
                if group in self._handoff_phases:
                    self._display.display(
                        f"TIMING HANDOFF PHASE [{group}] {self._handoff_phases[group]:.2f}s"
                    )
            self._handoff_active = False
            self._handoff_phases.clear()

    def v2_runner_on_ok(self, result):
        self._report(result)

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._report(result)

    def v2_runner_on_unreachable(self, result):
        self._report(result)

    def v2_runner_on_skipped(self, result):
        self._report(result)
