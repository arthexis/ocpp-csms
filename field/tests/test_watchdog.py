import json

import field.watchdog as watchdog_module
from field.state import FieldConfig, FieldState, load_state, save_state
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


def make_run(tmp_path, *, watchdog="disabled"):
    run_dir = tmp_path / "run"
    save_state(
        run_dir,
        FieldState(
            config=FieldConfig(
                charger="charger-a",
                legacy_service="legacy.service",
                csms_service="candidate.service",
                ocpp_command="ocpp-csms",
                listener_host="127.0.0.1",
                listener_port=12345,
                csms_data_dir=str(tmp_path / "data"),
                control_socket=str(tmp_path / "data" / "control.sock"),
            ),
            phase="configuration",
            watchdog=watchdog,
        ),
    )
    return run_dir


def test_enable_and_disable_only_change_watchdog_state(tmp_path):
    run_dir = make_run(tmp_path)

    set_enabled(run_dir, True)
    assert load_state(run_dir).watchdog == "enabled"
    assert load_state(run_dir).phase == "configuration"

    set_enabled(run_dir, False)
    assert load_state(run_dir).watchdog == "disabled"
    assert load_state(run_dir).phase == "configuration"
    assert len((run_dir / "watchdog-state.jsonl").read_text().splitlines()) == 2


def test_charger_disconnect_is_diagnostic_not_strong_failure(tmp_path, monkeypatch):
    run_dir = make_run(tmp_path, watchdog="enabled")
    monkeypatch.setattr(
        watchdog_module,
        "baseline_observation",
        lambda data_dir, charger: {
            "connected": False,
            "last_heartbeat": "earlier",
            "heartbeat_count": 4,
            "active_transactions": [],
        },
    )

    snapshot = health_snapshot(run_dir, Probe())

    assert snapshot["strong_failures"] == []
    assert snapshot["charger"]["connected"] is False


def test_strong_failures_are_debounced_before_rollback(tmp_path, monkeypatch):
    run_dir = make_run(tmp_path, watchdog="enabled")
    monkeypatch.setattr(
        watchdog_module,
        "baseline_observation",
        lambda data_dir, charger: {
            "connected": True,
            "last_heartbeat": "now",
            "heartbeat_count": 4,
            "active_transactions": [],
        },
    )
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


def test_healthy_check_resets_failure_count_and_once_does_not_rollback(tmp_path, monkeypatch):
    run_dir = make_run(tmp_path, watchdog="enabled")
    monkeypatch.setattr(
        watchdog_module,
        "baseline_observation",
        lambda data_dir, charger: {
            "connected": True,
            "last_heartbeat": "now",
            "heartbeat_count": 1,
            "active_transactions": [],
        },
    )
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
