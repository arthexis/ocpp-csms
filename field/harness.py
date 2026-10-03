from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from field.state import FieldConfig, FieldState, load_state, save_state, state_path
from field.system import LocalSystemProbe, SystemProbe


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
        "ocpp_command_exists": probe.executable_exists(config.ocpp_command),
        "csms_data_dir_ready": probe.directory_ready(config.csms_data_dir),
        "control_socket_parent_ready": probe.directory_ready(str(Path(config.control_socket).expanduser().parent)),
        "listener_port_valid": 1 <= config.listener_port <= 65535,
        "service_names_distinct": config.legacy_service != config.csms_service,
    }
    return {"ok": all(checks.values()), "checks": checks}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "status":
        print(json.dumps(load_state(Path(args.run_dir).expanduser()).to_dict(), indent=2, sort_keys=True))
        return 0
    return start_run(args, LocalSystemProbe())


if __name__ == "__main__":
    raise SystemExit(main())
