from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from field.evidence import baseline_observation
from field.protocol import (
    configuration_map,
    configuration_payload,
    evidence_checkpoint,
    reboot_observation,
    send_control,
)
from field.state import FieldConfig, FieldState, load_state, save_state, state_path
from field.system import LocalSystemProbe, SystemProbe

DEFAULT_CONFIG_KEYS = [
    "SupportedFeatureProfiles",
    "GetConfigurationMaxKeys",
    "HeartbeatInterval",
    "MeterValueSampleInterval",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m field.harness")
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start", help="Create or resume a field run and perform read-only preflight")
    start.add_argument("--run-dir", required=True)
    start.add_argument("--charger", required=True)
    start.add_argument("--legacy-service", required=True)
    start.add_argument("--csms-service", required=True)
    start.add_argument("--ocpp-command", required=True)
    start.add_argument("--listener-host", required=True)
    start.add_argument("--listener-port", required=True, type=int)
    start.add_argument("--csms-data-dir", required=True)
    start.add_argument("--control-socket", required=True)
    start.add_argument("--idle-confirmed", action="store_true")

    takeover_parser = commands.add_parser("takeover", help="Switch configured services and require an idle Heartbeat baseline")
    takeover_parser.add_argument("run_dir")
    takeover_parser.add_argument("--service-timeout", type=float, default=30.0)
    takeover_parser.add_argument("--baseline-timeout", type=float, default=90.0)
    takeover_parser.add_argument("--poll-interval", type=float, default=1.0)

    protocol = commands.add_parser("reboot-config", help="Reboot charger, require post-boot evidence, and collect configuration")
    protocol.add_argument("run_dir")
    protocol.add_argument("--reboot-timeout", type=float, default=60.0)
    protocol.add_argument("--post-boot-timeout", type=float, default=120.0)
    protocol.add_argument("--repeat-delay", type=float, default=30.0)
    protocol.add_argument("--poll-interval", type=float, default=1.0)
    protocol.add_argument("--key", action="append", dest="keys")

    rollback_parser = commands.add_parser("rollback", help="Restore the configured legacy service")
    rollback_parser.add_argument("run_dir")
    rollback_parser.add_argument("--reason", default="operator_requested")
    rollback_parser.add_argument("--service-timeout", type=float, default=30.0)
    rollback_parser.add_argument("--poll-interval", type=float, default=1.0)

    status = commands.add_parser("status", help="Show stored field-run state")
    status.add_argument("run_dir")
    return parser


def config_from_args(args: argparse.Namespace) -> FieldConfig:
    return FieldConfig(
        charger=args.charger,
        legacy_service=args.legacy_service,
        csms_service=args.csms_service,
        ocpp_command=args.ocpp_command,
        listener_host=args.listener_host,
        listener_port=args.listener_port,
        csms_data_dir=args.csms_data_dir,
        control_socket=args.control_socket,
    )


def preflight(config: FieldConfig, *, idle_confirmed: bool, probe: SystemProbe) -> dict[str, Any]:
    checks = {
        "idle_confirmed": idle_confirmed,
        "legacy_service_exists": probe.service_exists(config.legacy_service),
        "csms_service_exists": probe.service_exists(config.csms_service),
        "ocpp_command_exists": probe.executable_exists(config.ocpp_command),
        "csms_data_dir_ready": probe.directory_ready(config.csms_data_dir),
        "control_socket_parent_ready": probe.directory_ready(str(Path(config.control_socket).expanduser().parent)),
        "listener_port_valid": 1 <= config.listener_port <= 65535,
        "service_names_distinct": config.legacy_service != config.csms_service,
    }
    return {"ok": all(checks.values()), "checks": checks}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _wait(predicate, *, timeout: float, interval: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def start_run(args: argparse.Namespace, probe: SystemProbe) -> int:
    run_dir = Path(args.run_dir).expanduser()
    config = config_from_args(args)
    if state_path(run_dir).exists():
        state = load_state(run_dir)
        if state.config != config:
            raise ValueError("existing field run uses different configuration")
    else:
        state = FieldState(config=config)
        save_state(run_dir, state)
    result = preflight(config, idle_confirmed=args.idle_confirmed, probe=probe)
    _write_json(run_dir / "preflight.json", result)
    if not result["ok"]:
        return 1
    save_state(run_dir, replace(state, phase="preflight"))
    return 0


def rollback(run_dir: Path, probe: SystemProbe, *, reason: str, service_timeout: float, poll_interval: float) -> int:
    state = load_state(run_dir)
    config = state.config
    actions: list[dict[str, Any]] = []
    if probe.service_active(config.csms_service):
        actions.append({"stop_csms": probe.service_stop(config.csms_service)})
    else:
        actions.append({"stop_csms": "already_stopped"})
    port_free = _wait(lambda: not probe.port_listening(config.listener_host, config.listener_port), timeout=service_timeout, interval=poll_interval)
    actions.append({"port_free": port_free})
    legacy_started = False
    if port_free:
        if probe.service_active(config.legacy_service):
            legacy_started = True
            actions.append({"start_legacy": "already_active"})
        else:
            legacy_started = probe.service_start(config.legacy_service)
            actions.append({"start_legacy": legacy_started})
    legacy_ready = False
    if port_free and legacy_started:
        legacy_ready = _wait(lambda: probe.service_active(config.legacy_service) and probe.port_listening(config.listener_host, config.listener_port), timeout=service_timeout, interval=poll_interval)
    ok = bool(port_free and legacy_started and legacy_ready)
    _write_json(run_dir / "rollback.json", {"ok": ok, "reason": reason, "actions": actions, "legacy_ready": legacy_ready})
    save_state(run_dir, replace(state, phase="rolled_back" if ok else "rollback_failed"))
    return 0 if ok else 1


def takeover(run_dir: Path, probe: SystemProbe, *, service_timeout: float, baseline_timeout: float, poll_interval: float) -> int:
    state = load_state(run_dir)
    config = state.config
    if state.phase not in {"preflight", "baseline"}:
        raise ValueError(f"takeover requires preflight state, got {state.phase}")
    if state.phase == "baseline":
        return 0
    if not probe.service_active(config.legacy_service):
        _write_json(run_dir / "takeover.json", {"ok": False, "error": "legacy_not_active"})
        return 1
    save_state(run_dir, replace(state, phase="takeover"))
    if not probe.service_stop(config.legacy_service):
        return rollback(run_dir, probe, reason="legacy_stop_failed", service_timeout=service_timeout, poll_interval=poll_interval)
    if not _wait(lambda: not probe.service_active(config.legacy_service) and not probe.port_listening(config.listener_host, config.listener_port), timeout=service_timeout, interval=poll_interval):
        return rollback(run_dir, probe, reason="legacy_did_not_release_listener", service_timeout=service_timeout, poll_interval=poll_interval)
    if not probe.service_start(config.csms_service):
        return rollback(run_dir, probe, reason="csms_start_failed", service_timeout=service_timeout, poll_interval=poll_interval)
    if not _wait(lambda: probe.service_active(config.csms_service) and probe.port_listening(config.listener_host, config.listener_port) and probe.socket_exists(config.control_socket), timeout=service_timeout, interval=poll_interval):
        return rollback(run_dir, probe, reason="csms_not_ready", service_timeout=service_timeout, poll_interval=poll_interval)

    observation: dict[str, Any] = {}
    def baseline_ready() -> bool:
        nonlocal observation
        observation = baseline_observation(config.csms_data_dir, config.charger)
        return bool(observation["connected"] and observation["heartbeat_count"] > 0 and not observation["active_transactions"])
    if not _wait(baseline_ready, timeout=baseline_timeout, interval=poll_interval):
        _write_json(run_dir / "baseline.json", {"ok": False, "observation": observation})
        return rollback(run_dir, probe, reason="baseline_not_established", service_timeout=service_timeout, poll_interval=poll_interval)
    _write_json(run_dir / "baseline.json", {"ok": True, "observation": observation})
    _write_json(run_dir / "takeover.json", {"ok": True})
    save_state(run_dir, replace(load_state(run_dir), phase="baseline"))
    return 0


def _accepted(response: dict[str, Any]) -> bool:
    payload = response.get("response")
    return response.get("ok") is True and isinstance(payload, dict) and payload.get("status") == "Accepted"


def reboot_and_configure(run_dir: Path, *, reboot_timeout: float, post_boot_timeout: float, repeat_delay: float, poll_interval: float, keys: list[str] | None) -> int:
    state = load_state(run_dir)
    config = state.config
    if state.phase not in {"baseline", "configuration"}:
        raise ValueError(f"reboot-config requires baseline state, got {state.phase}")
    if state.phase == "configuration":
        return 0

    event_id, runtime_id = evidence_checkpoint(config.csms_data_dir)
    soft = send_control(config.control_socket, {"command": "reboot", "charger": config.charger, "type": "Soft"})
    attempts = [{"type": "Soft", "response": soft}]
    if not _accepted(soft):
        _write_json(run_dir / "reboot.json", {"ok": False, "attempts": attempts, "error": "soft_reset_not_accepted"})
        return 1

    observation: dict[str, Any] = {}
    def reboot_started() -> bool:
        nonlocal observation
        observation = reboot_observation(config.csms_data_dir, config.charger, after_event_id=event_id, after_runtime_id=runtime_id)
        return bool(observation["disconnect_seen"])
    if not _wait(reboot_started, timeout=reboot_timeout, interval=poll_interval):
        hard = send_control(config.control_socket, {"command": "reboot", "charger": config.charger, "type": "Hard"})
        attempts.append({"type": "Hard", "response": hard})
        if not _accepted(hard):
            _write_json(run_dir / "reboot.json", {"ok": False, "attempts": attempts, "error": "hard_reset_not_accepted"})
            return 1

    def post_boot_ready() -> bool:
        nonlocal observation
        observation = reboot_observation(config.csms_data_dir, config.charger, after_event_id=event_id, after_runtime_id=runtime_id)
        return bool(observation["disconnect_seen"] and observation["reconnect_seen"] and observation["boot_notification"] and observation["heartbeat"])
    if not _wait(post_boot_ready, timeout=post_boot_timeout, interval=poll_interval):
        _write_json(run_dir / "reboot.json", {"ok": False, "attempts": attempts, "observation": observation, "error": "post_boot_evidence_incomplete"})
        return 1

    _write_json(run_dir / "reboot.json", {"ok": True, "attempts": attempts, "observation": observation})
    save_state(run_dir, replace(state, phase="post_boot"))

    selected_keys = keys or DEFAULT_CONFIG_KEYS
    config_dir = run_dir / "config"
    all_payload = configuration_payload(send_control(config.control_socket, {"command": "config", "charger": config.charger}))
    selected_payload = configuration_payload(send_control(config.control_socket, {"command": "config", "charger": config.charger, "keys": selected_keys}))
    _write_json(config_dir / "all.json", all_payload)
    _write_json(config_dir / "selected.json", selected_payload)
    if repeat_delay > 0:
        time.sleep(repeat_delay)
    repeated_payload = configuration_payload(send_control(config.control_socket, {"command": "config", "charger": config.charger, "keys": selected_keys}))
    _write_json(config_dir / "repeat.json", repeated_payload)

    before = configuration_map(selected_payload)
    after = configuration_map(repeated_payload)
    changed = {key: {"before": before.get(key), "after": after.get(key)} for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)}
    baseline = baseline_observation(config.csms_data_dir, config.charger)
    ok = bool(baseline["connected"] and baseline["heartbeat_count"] > 0 and not baseline["active_transactions"])
    _write_json(run_dir / "configuration.json", {"ok": ok, "keys": selected_keys, "changed": changed, "unknown": repeated_payload.get("unknown_key", []), "observation": baseline})
    if not ok:
        return 1
    save_state(run_dir, replace(load_state(run_dir), phase="configuration"))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    probe = LocalSystemProbe()
    if args.command == "status":
        print(json.dumps(load_state(Path(args.run_dir).expanduser()).to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "takeover":
        return takeover(Path(args.run_dir).expanduser(), probe, service_timeout=args.service_timeout, baseline_timeout=args.baseline_timeout, poll_interval=args.poll_interval)
    if args.command == "reboot-config":
        return reboot_and_configure(Path(args.run_dir).expanduser(), reboot_timeout=args.reboot_timeout, post_boot_timeout=args.post_boot_timeout, repeat_delay=args.repeat_delay, poll_interval=args.poll_interval, keys=args.keys)
    if args.command == "rollback":
        return rollback(Path(args.run_dir).expanduser(), probe, reason=args.reason, service_timeout=args.service_timeout, poll_interval=args.poll_interval)
    return start_run(args, probe)


if __name__ == "__main__":
    raise SystemExit(main())
