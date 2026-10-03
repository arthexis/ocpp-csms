from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from field.evidence import baseline_observation
from field.harness import rollback
from field.state import load_state, save_state
from field.system import LocalSystemProbe, SystemProbe


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m field.watchdog")
    commands = parser.add_subparsers(dest="command", required=True)

    for name in ("enable", "disable", "status"):
        command = commands.add_parser(name)
        command.add_argument("run_dir")

    run = commands.add_parser("run", help="Run watchdog checks while watchdog state is enabled")
    run.add_argument("run_dir")
    run.add_argument("--interval", type=float, default=30.0)
    run.add_argument("--failure-threshold", type=int, default=3)
    run.add_argument("--rollback-timeout", type=float, default=30.0)
    run.add_argument("--rollback-poll-interval", type=float, default=1.0)
    run.add_argument("--once", action="store_true", help="Perform one check and exit")
    return parser


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")


def set_enabled(run_dir: Path, enabled: bool) -> None:
    state = load_state(run_dir)
    save_state(run_dir, replace(state, watchdog="enabled" if enabled else "disabled"))
    _append_jsonl(
        run_dir / "watchdog-state.jsonl",
        {"timestamp": utc_now(), "watchdog": "enabled" if enabled else "disabled"},
    )


def health_snapshot(run_dir: Path, probe: SystemProbe) -> dict[str, Any]:
    state = load_state(run_dir)
    config = state.config
    observation: dict[str, Any]
    try:
        observation = baseline_observation(config.csms_data_dir, config.charger)
    except Exception as exc:
        observation = {"error": type(exc).__name__}

    service_active = probe.service_active(config.csms_service)
    listener = probe.port_listening(config.listener_host, config.listener_port)
    control_socket = probe.socket_exists(config.control_socket)
    strong_failures = []
    if not service_active:
        strong_failures.append("csms_service_inactive")
    if not listener:
        strong_failures.append("listener_unavailable")
    if not control_socket:
        strong_failures.append("control_socket_unavailable")

    return {
        "timestamp": utc_now(),
        "watchdog": state.watchdog,
        "phase": state.phase,
        "csms_service_active": service_active,
        "listener_available": listener,
        "control_socket_available": control_socket,
        "strong_failures": strong_failures,
        "charger": {
            "connected": observation.get("connected"),
            "last_heartbeat": observation.get("last_heartbeat"),
            "heartbeat_count": observation.get("heartbeat_count"),
            "active_transactions": observation.get("active_transactions"),
            "evidence_error": observation.get("error"),
        },
    }


def run_watchdog(
    run_dir: Path,
    probe: SystemProbe,
    *,
    interval: float,
    failure_threshold: int,
    rollback_timeout: float,
    rollback_poll_interval: float,
    once: bool = False,
) -> int:
    if failure_threshold < 1:
        raise ValueError("failure threshold must be at least 1")
    if interval < 0 or rollback_timeout < 0 or rollback_poll_interval < 0:
        raise ValueError("watchdog timing values must be non-negative")

    consecutive_failures = 0
    while True:
        state = load_state(run_dir)
        if state.watchdog != "enabled":
            return 0

        snapshot = health_snapshot(run_dir, probe)
        failures = snapshot["strong_failures"]
        if failures:
            consecutive_failures += 1
        else:
            consecutive_failures = 0
        snapshot["consecutive_strong_failures"] = consecutive_failures
        snapshot["failure_threshold"] = failure_threshold
        _append_jsonl(run_dir / "watchdog-snapshots.jsonl", snapshot)
        _write_json(run_dir / "watchdog-latest.json", snapshot)

        if consecutive_failures >= failure_threshold:
            reason = "watchdog:" + ",".join(failures)
            result = rollback(
                run_dir,
                probe,
                reason=reason,
                service_timeout=rollback_timeout,
                poll_interval=rollback_poll_interval,
            )
            post = load_state(run_dir)
            save_state(run_dir, replace(post, watchdog="disabled"))
            _append_jsonl(
                run_dir / "watchdog-state.jsonl",
                {"timestamp": utc_now(), "watchdog": "disabled", "reason": "automatic_rollback"},
            )
            return result

        if once:
            return 0
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_dir = Path(args.run_dir).expanduser()
    if args.command == "enable":
        set_enabled(run_dir, True)
        return 0
    if args.command == "disable":
        set_enabled(run_dir, False)
        return 0
    if args.command == "status":
        state = load_state(run_dir)
        latest = run_dir / "watchdog-latest.json"
        payload: dict[str, Any] = {"watchdog": state.watchdog, "phase": state.phase}
        if latest.exists():
            payload["latest"] = json.loads(latest.read_text())
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    return run_watchdog(
        run_dir,
        LocalSystemProbe(),
        interval=args.interval,
        failure_threshold=args.failure_threshold,
        rollback_timeout=args.rollback_timeout,
        rollback_poll_interval=args.rollback_poll_interval,
        once=args.once,
    )


if __name__ == "__main__":
    raise SystemExit(main())
