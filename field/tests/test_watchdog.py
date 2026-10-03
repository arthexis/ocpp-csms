import json

import field.watchdog as watchdog_module
from field.state import load_state
from field.watchdog import health_snapshot, run_watchdog, set_enabled


class Probe:
    def __init__(self, *, service=True, listener=True, socket=True):
        self.service = service
        self.listener = listener
        self.socket = socket

    def service_active(self, service):
        return self.service

    def port_listening(self, host, port):
        return self.listener

    def socket_exists(self, path):
        return self.socket


def observation(*, connected=True, heartbeat_count=1):
    return {
        "connected": connected,
        "last_heartbeat": "now" if heartbeat_count else None,
        "heartbeat_count": heartbeat_count,
        "active_transactions": [],
    }


def test_enable_and_disable_only_change_watchdog_state(field_run):
    run_dir = field_run()

    set_enabled(run_dir, True)
    enabled = load_state(run_dir)
    assert enabled.watchdog == "enabled"
    assert enabled.phase == "configuration"

    set_enabled(run_dir, False)
    disabled = load_state(run_dir)
    assert disabled.watchdog == "disabled"
    assert disabled.phase == "configuration"
    assert len((run_dir / "watchdog-state.jsonl").read_text().splitlines()) == 2


def test_charger_disconnect_is_diagnostic_not_strong_failure(field_run, monkeypatch):
    run_dir = field_run(watchdog="enabled")
    monkeypatch.setattr(watchdog_module, "baseline_observation", lambda *args: observation(connected=False, heartbeat_count=4))

    snapshot = health_snapshot(run_dir, Probe())

    assert snapshot["strong_failures"] == []
    assert snapshot["charger"]["connected"] is False


def test_strong_failures_are_debounced_before_rollback(field_run, monkeypatch):
    run_dir = field_run(watchdog="enabled")
    monkeypatch.setattr(watchdog_module, "baseline_observation", lambda *args: observation(heartbeat_count=4))
    calls = []

    def fake_rollback(run_dir, probe, **kwargs):
        calls.append(kwargs["reason"])
        return 0

    monkeypatch.setattr(watchdog_module, "rollback", fake_rollback)

    assert run_watchdog(
        run_dir,
        Probe(service=False, listener=False, socket=False),
        interval=0,
        failure_threshold=2,
        rollback_timeout=0,
        rollback_poll_interval=0,
    ) == 0

    assert len(calls) == 1
    assert load_state(run_dir).watchdog == "disabled"
    snapshots = [json.loads(line) for line in (run_dir / "watchdog-snapshots.jsonl").read_text().splitlines()]
    assert [item["consecutive_strong_failures"] for item in snapshots] == [1, 2]


def test_healthy_check_resets_failure_count_and_once_does_not_rollback(field_run, monkeypatch):
    run_dir = field_run(watchdog="enabled")
    monkeypatch.setattr(watchdog_module, "baseline_observation", lambda *args: observation())
    monkeypatch.setattr(watchdog_module, "rollback", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("rollback")))

    assert run_watchdog(
        run_dir,
        Probe(),
        interval=0,
        failure_threshold=2,
        rollback_timeout=0,
        rollback_poll_interval=0,
        once=True,
    ) == 0

    latest = json.loads((run_dir / "watchdog-latest.json").read_text())
    assert latest["consecutive_strong_failures"] == 0
    assert latest["strong_failures"] == []
