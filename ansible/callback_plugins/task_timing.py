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


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "task_timing"
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self):
        super().__init__()
        self._started: dict[str, float] = {}

    def v2_playbook_on_task_start(self, task, is_conditional):
        self._started[task._uuid] = time.monotonic()

    def _report(self, result):
        task = result._task
        started = self._started.pop(task._uuid, None)
        if started is None:
            return
        elapsed = time.monotonic() - started
        self._display.display(f"TIMING [{task.get_name()}] {elapsed:.2f}s")

    def v2_runner_on_ok(self, result):
        self._report(result)

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._report(result)

    def v2_runner_on_unreachable(self, result):
        self._report(result)

    def v2_runner_on_skipped(self, result):
        self._report(result)
