from argparse import Namespace
from dataclasses import replace

import pytest

import field.harness as harness_module
from field.harness import (
    build_report,
    config_from_args,
    enter_soak,
    handoff,
    preflight,
    reboot_and_configure,
    refresh,
    rollback,
    start_run,
    takeover,
)
from field.state import FieldConfig, load_state, save_state


class Probe:
    def __init__(self, *, service=True, executable=True, directory=True):
        self.service = service
        self.executable = executable
        self.directory = directory
        self.active = {
            "legacy-example.service": True,
            "candidate-example.service": False,
        }
        self.listener = True
        self.socket = False
        self.actions = []

    def executable_exists(self, command):
        return self.executable

    def service_exists(self, service):
        return self.service

    def service_active(self, service):
        return self.active.get(service, False)

    def service_start(self, service):
        self.actions.append(("start", service))
        self.active[service] = True
        if service == "candidate-example.service":
            self.socket = True
        self.listener = True
        return True

    def service_stop(self, service):
        self.actions.append(("stop", service))
        self.active[service] = False
        self.listener = any(self.active.values())
        if service == "candidate-example.service":
            self.socket = False
        return True

    def directory_ready(self, path):
        return self.directory

    def port_listening(self, host, port):
        return self.listener

    def socket_exists(self, path):
        return self.socket


def args(tmp_path, **overrides):
    values = {
        "run_dir": str(tmp_path / "run"),
        "charger": "charger-a",
        "legacy_service": "legacy-example.service",
        "csms_service": "candidate-example.service",
        "ocpp_command": "/opt/example/bin/ocpp-csms",
        "listener_host": "127.0.0.1",
        "listener_port": 12345,
        "csms_data_dir": str(tmp_path / "data"),
        "control_socket": str(tmp_path / "data" / "control.sock"),
        "idle_confirmed": True,
    }
    values.update(overrides)
    return Namespace(**values)


def initialized_run(tmp_path, probe=None):
    probe = probe or Probe()
    assert start_run(args(tmp_path), probe) == 0
    return tmp_path / "run", probe


def test_operational_values_come_from_arguments(tmp_path):
    parsed = args(tmp_path)
    config = config_from_args(parsed)
    assert config == FieldConfig(
        charger="charger-a",
        legacy_service="legacy-example.service",
        csms_service="candidate-example.service",
        ocpp_command="/opt/example/bin/ocpp-csms",
        listener_host="127.0.0.1",
        listener_port=12345,
        csms_data_dir=str(tmp_path / "data"),
        control_socket=str(tmp_path / "data" / "control.sock"),
    )


@pytest.mark.parametrize(
    ("overrides", "probe", "failed_check"),
    [
        ({"idle_confirmed": False}, Probe(), "idle_confirmed"),
        ({}, Probe(service=False), "legacy_service_exists"),
        ({}, Probe(service=False), "csms_service_exists"),
        ({}, Probe(executable=False), "ocpp_command_exists"),
        ({}, Probe(directory=False), "csms_data_dir_ready"),
        ({"listener_port": 0}, Probe(), "listener_port_valid"),
        ({"csms_service": "legacy-example.service"}, Probe(), "service_names_distinct"),
    ],
)
def test_preflight_reports_failed_invariants(tmp_path, overrides, probe, failed_check):
    parsed = args(tmp_path, **overrides)
    result = preflight(config_from_args(parsed), idle_confirmed=parsed.idle_confirmed, probe=probe)
    assert result["ok"] is False
    assert result["checks"][failed_check] is False


def test_successful_start_records_preflight_state(tmp_path):
    parsed = args(tmp_path)
    assert start_run(parsed, Probe()) == 0
    state = load_state(tmp_path / "run")
    assert state.phase == "preflight"
    assert (tmp_path / "run" / "preflight.json").exists()


def test_existing_run_refuses_configuration_change(tmp_path):
    assert start_run(args(tmp_path), Probe()) == 0
    with pytest.raises(ValueError):
        start_run(args(tmp_path, listener_port=54321), Probe())


def test_takeover_switches_configured_services_and_records_baseline(tmp_path, monkeypatch):
    run_dir, probe = initialized_run(tmp_path)
    monkeypatch.setattr(harness_module, "baseline_observation", lambda data_dir, charger: {"connected": True, "heartbeat_count": 2, "last_heartbeat": "now", "active_transactions": []})
    assert takeover(run_dir, probe, service_timeout=0, baseline_timeout=0, poll_interval=0) == 0
    assert probe.actions == [("stop", "legacy-example.service"), ("start", "candidate-example.service")]
    assert load_state(run_dir).phase == "baseline"
    assert (run_dir / "baseline.json").exists()


def test_failed_baseline_uses_common_rollback(tmp_path, monkeypatch):
    run_dir, probe = initialized_run(tmp_path)
    monkeypatch.setattr(harness_module, "baseline_observation", lambda data_dir, charger: {"connected": True, "heartbeat_count": 0, "last_heartbeat": None, "active_transactions": []})
    assert takeover(run_dir, probe, service_timeout=0, baseline_timeout=0, poll_interval=0) == 0
    assert probe.actions == [
        ("stop", "legacy-example.service"),
        ("start", "candidate-example.service"),
        ("stop", "candidate-example.service"),
        ("start", "legacy-example.service"),
    ]
    assert load_state(run_dir).phase == "rolled_back"


def test_rollback_is_idempotent_when_candidate_is_already_stopped(tmp_path):
    run_dir, probe = initialized_run(tmp_path)
    probe.active["legacy-example.service"] = False
    probe.listener = False
    assert rollback(run_dir, probe, reason="test", service_timeout=0, poll_interval=0) == 0
    assert rollback(run_dir, probe, reason="test_again", service_timeout=0, poll_interval=0) == 0
    assert probe.active["legacy-example.service"] is True
    assert load_state(run_dir).watchdog == "disabled"


def test_refresh_rebaselines_running_candidate_and_archives_previous_attempt(tmp_path, monkeypatch):
    run_dir, probe = initialized_run(tmp_path)
    probe.active["legacy-example.service"] = False
    probe.active["candidate-example.service"] = True
    probe.listener = True
    probe.socket = True
    state = load_state(run_dir)
    save_state(run_dir, replace(state, phase="idle_soak", watchdog="disabled"))
    (run_dir / "baseline.json").write_text('{"ok": true, "old": true}\n')
    (run_dir / "configuration.json").write_text('{"ok": true}\n')
    config_dir = run_dir / "config"
    config_dir.mkdir()
    (config_dir / "all.json").write_text('{"configuration_key": []}\n')
    monkeypatch.setattr(
        harness_module,
        "baseline_observation",
        lambda *a: {"connected": True, "heartbeat_count": 4, "last_heartbeat": "new", "active_transactions": []},
    )

    assert refresh(run_dir, probe, baseline_timeout=0, poll_interval=0) == 0

    state = load_state(run_dir)
    assert state.phase == "baseline"
    assert state.watchdog == "disabled"
    assert (run_dir / "attempts" / "001" / "baseline.json").exists()
    assert (run_dir / "attempts" / "001" / "configuration.json").exists()
    assert (run_dir / "attempts" / "001" / "config" / "all.json").exists()
    assert (run_dir / "baseline.json").exists()
    assert '"source": "refresh"' in (run_dir / "baseline.json").read_text()
    assert (run_dir / "refresh.json").exists()
    assert probe.actions == []


def test_refresh_requires_watchdog_disabled(tmp_path):
    run_dir, probe = initialized_run(tmp_path)
    probe.active["legacy-example.service"] = False
    probe.active["candidate-example.service"] = True
    probe.socket = True
    state = load_state(run_dir)
    save_state(run_dir, replace(state, phase="idle_soak", watchdog="enabled"))

    with pytest.raises(ValueError, match="watchdog"):
        refresh(run_dir, probe, baseline_timeout=0, poll_interval=0)


def test_refresh_refuses_unhealthy_or_legacy_served_state(tmp_path):
    run_dir, probe = initialized_run(tmp_path)
    state = load_state(run_dir)
    save_state(run_dir, replace(state, phase="configuration", watchdog="disabled"))

    assert refresh(run_dir, probe, baseline_timeout=0, poll_interval=0) == 1
    assert load_state(run_dir).phase == "configuration"
    assert (run_dir / "refresh.json").exists()


def test_reboot_config_uses_one_hard_fallback_and_structured_configuration(tmp_path, monkeypatch):
    run_dir, probe = initialized_run(tmp_path)
    monkeypatch.setattr(harness_module, "baseline_observation", lambda *a: {"connected": True, "heartbeat_count": 2, "last_heartbeat": "now", "active_transactions": []})
    assert takeover(run_dir, probe, service_timeout=0, baseline_timeout=0, poll_interval=0) == 0
    config = load_state(run_dir).config

    calls = []
    responses = iter([
        {"ok": True, "response": {"status": "Accepted"}},
        {"ok": True, "response": {"status": "Accepted"}},
        {"ok": True, "response": {"configuration_key": [], "unknown_key": []}},
        {"ok": True, "response": {"configuration_key": [{"key": "HeartbeatInterval", "readonly": False, "value": "300"}], "unknown_key": []}},
        {"ok": True, "response": {"configuration_key": [{"key": "HeartbeatInterval", "readonly": False, "value": "300"}], "unknown_key": []}},
    ])
    monkeypatch.setattr(harness_module, "evidence_checkpoint", lambda data_dir: (10, 20))
    monkeypatch.setattr(harness_module, "send_control", lambda path, request: calls.append((path, request)) or next(responses))
    observations = iter([
        {"disconnect_seen": False, "reconnect_seen": False, "boot_notification": False, "heartbeat": False, "actions": []},
        {"disconnect_seen": True, "reconnect_seen": True, "boot_notification": True, "heartbeat": True, "actions": ["BootNotification", "Heartbeat"]},
    ])
    monkeypatch.setattr(harness_module, "reboot_observation", lambda *a, **k: next(observations))
    monkeypatch.setattr(harness_module, "_wait", lambda predicate, **kwargs: predicate())

    assert reboot_and_configure(run_dir, reboot_timeout=0, post_boot_timeout=0, repeat_delay=0, poll_interval=0, keys=["HeartbeatInterval"]) == 0
    reboot_requests = [request for _, request in calls if request.get("command") == "reboot"]
    assert [request["type"] for request in reboot_requests] == ["Soft", "Hard"]
    assert all(path == config.control_socket for path, _ in calls)
    assert load_state(run_dir).phase == "configuration"
    assert (run_dir / "config" / "all.json").exists()
    assert (run_dir / "configuration.json").exists()


def test_soak_arms_watchdog_only_after_idle_candidate_checks(tmp_path, monkeypatch):
    run_dir, probe = initialized_run(tmp_path)
    probe.active["legacy-example.service"] = False
    probe.active["candidate-example.service"] = True
    probe.listener = True
    probe.socket = True
    state = load_state(run_dir)
    save_state(run_dir, replace(state, phase="configuration"))
    monkeypatch.setattr(harness_module, "baseline_observation", lambda *a: {"connected": True, "heartbeat_count": 3, "last_heartbeat": "now", "active_transactions": []})

    assert enter_soak(run_dir, probe) == 0

    state = load_state(run_dir)
    assert state.phase == "idle_soak"
    assert state.watchdog == "enabled"
    assert probe.actions == []


def test_handoff_disarms_watchdog_without_switching_services(tmp_path):
    run_dir, probe = initialized_run(tmp_path)
    probe.active["legacy-example.service"] = False
    probe.active["candidate-example.service"] = True
    probe.listener = True
    probe.socket = True
    state = load_state(run_dir)
    save_state(run_dir, replace(state, phase="idle_soak", watchdog="enabled"))

    assert handoff(run_dir, probe) == 0

    state = load_state(run_dir)
    assert state.phase == "handed_off"
    assert state.watchdog == "disabled"
    assert probe.actions == []


def test_report_marks_protocol_complete_only_after_successful_handoff(tmp_path):
    run_dir, probe = initialized_run(tmp_path)
    probe.active["legacy-example.service"] = False
    probe.active["candidate-example.service"] = True
    probe.listener = True
    probe.socket = True
    for name in ("preflight", "takeover", "baseline", "reboot", "configuration", "soak", "handoff"):
        (run_dir / f"{name}.json").write_text('{"ok": true}\n')
    state = load_state(run_dir)
    save_state(run_dir, replace(state, phase="handed_off", watchdog="disabled"))

    report = build_report(run_dir, probe)

    assert report["protocol_44_complete"] is True
    assert report["next_protocol"] == 45
    assert (run_dir / "result.json").exists()
