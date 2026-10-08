from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from ocpp_forwarder.collector import CollectorClient
from ocpp_forwarder.forwarder import Forwarder
from ocpp_forwarder.state import StateStore


def _token(path: str) -> str:
    value = Path(path).expanduser().read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"empty OCPP Collector token file: {path}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocpp-forwarder",
        description="Forward OCPP-CSMS export pages to an OCPP Collector.",
    )
    parser.add_argument(
        "--satellite-id",
        default=os.environ.get("OCPP_FORWARDER_SATELLITE_ID", ""),
    )
    parser.add_argument(
        "--collector-url",
        default=os.environ.get(
            "OCPP_FORWARDER_COLLECTOR_URL",
            "https://ocpp-collector.arthexis.com",
        ),
    )
    parser.add_argument(
        "--token-file",
        default=os.environ.get(
            "OCPP_FORWARDER_TOKEN_FILE",
            "/etc/ocpp-forwarder/token",
        ),
    )
    parser.add_argument(
        "--state-file",
        default=os.environ.get(
            "OCPP_FORWARDER_STATE_FILE",
            "/var/lib/ocpp-forwarder/state.json",
        ),
    )
    parser.add_argument(
        "--csms-command",
        default=os.environ.get("OCPP_FORWARDER_CSMS_COMMAND", "/usr/local/bin/ocpp-csms"),
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get(
            "OCPP_FORWARDER_DATA_DIR",
            str(Path.home() / "ocpp-csms-data"),
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.environ.get("OCPP_FORWARDER_BATCH_SIZE", "500")),
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.environ.get("OCPP_FORWARDER_POLL_SECONDS", "10")),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("OCPP_FORWARDER_TIMEOUT", "15")),
    )

    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run the forwarding loop")
    run.add_argument("--once", action="store_true", help="Forward at most one export page")
    commands.add_parser("status", help="Show local forwarding state")
    return parser


def _validate(args: argparse.Namespace) -> None:
    if not args.satellite_id:
        raise ValueError("--satellite-id is required")
    if args.batch_size < 1:
        raise ValueError("--batch-size must be at least 1")
    if args.poll_seconds < 0:
        raise ValueError("--poll-seconds must not be negative")
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        _validate(args)
        store = StateStore(args.state_file)
        if args.command == "status":
            state = store.load()
            print(f"Satellite: {args.satellite_id}")
            print(f"Source: {state.source_id or '-'}")
            print(f"Cursor: {state.cursor}")
            print(f"Last upload: {state.last_success_at or '-'}")
            print(f"Last error: {state.last_error or '-'}")
            return 0

        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        collector = CollectorClient(
            args.collector_url,
            _token(args.token_file),
            timeout=args.timeout,
        )
        forwarder = Forwarder(
            satellite_id=args.satellite_id,
            csms_command=args.csms_command,
            data_dir=args.data_dir,
            batch_size=args.batch_size,
            state_store=store,
            collector=collector,
        )
        if args.once:
            forwarder.forward_once()
            return 0
        forwarder.run(poll_seconds=args.poll_seconds)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
