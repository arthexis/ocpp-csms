from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import time
from pathlib import Path

from field import redirect as redirect_tools
from field.discover import discover_existing_endpoint, require_root
from field.redirect import RedirectReceipt, receipt_from_json
from ocpp_csms.install_cutover import connection_markers, wait_for_reconnect
from ocpp_csms.install_preflight import evaluate_preflight
from ocpp_csms.schema import DATABASE_FILENAME

_HANDOFF_RECEIPT = "handoff-endpoint.json"
_REDIRECT_RECEIPT = "redirect.json"


def receipt_path(state_dir: str | Path) -> Path:
    return Path(state_dir).expanduser() / _HANDOFF_RECEIPT


def redirect_path(state_dir: str | Path) -> Path:
    return Path(state_dir).expanduser() / _REDIRECT_RECEIPT


def _write_receipt(path: Path, receipt: RedirectReceipt) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(receipt.to_json(), handle, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError:
        raise RuntimeError("handoff_receipt_exists") from None


def load_receipt(state_dir: str | Path) -> RedirectReceipt:
    path = receipt_path(state_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return receipt_from_json(payload)
    except FileNotFoundError:
        raise RuntimeError("handoff_receipt_not_found") from None
    except (json.JSONDecodeError, ValueError):
        raise RuntimeError("invalid_handoff_receipt") from None


def observe_existing_endpoint(
    *,
    state_dir: str | Path,
    interface: str,
    listen_port: int,
    seconds: float,
    capture_log: str | Path | None = None,
) -> RedirectReceipt:
    """Observe and persist a validated existing endpoint without network mutation."""
    require_root()
    path = receipt_path(state_dir)
    if path.exists():
        raise RuntimeError("handoff_receipt_exists")
    receipt = discover_existing_endpoint(
        interface=interface,
        listen_port=listen_port,
        seconds=seconds,
        capture_log=capture_log,
    )
    if receipt is None:
        raise RuntimeError("no_existing_endpoint_websocket_upgrade")
    _write_receipt(path, receipt)
    return receipt


def _ocpp_markers(data_dir: str | Path, expected: tuple[str, ...]) -> dict[str, int]:
    markers = {charger: 0 for charger in expected}
    database = Path(data_dir).expanduser() / DATABASE_FILENAME
    if not expected or not database.exists():
        return markers
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        for charger in expected:
            row = connection.execute(
                "SELECT COALESCE(MAX(id), 0) FROM events WHERE charger_id = ? AND direction = 'in'",
                (charger,),
            ).fetchone()
            markers[charger] = int(row[0]) if row else 0
    return markers


def _fresh_ocpp(data_dir: Path, markers: dict[str, int]) -> set[str]:
    database = data_dir / DATABASE_FILENAME
    if not database.exists():
        return set()
    fresh: set[str] = set()
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        for charger, marker in markers.items():
            row = connection.execute(
                "SELECT COALESCE(MAX(id), 0) FROM events WHERE charger_id = ? AND direction = 'in'",
                (charger,),
            ).fetchone()
            if row and int(row[0]) > marker:
                fresh.add(charger)
    return fresh


def wait_for_fresh_ocpp(
    data_dir: str | Path,
    markers: dict[str, int],
    *,
    timeout: float = 30.0,
    interval: float = 0.5,
) -> tuple[str, ...]:
    expected = set(markers)
    if not expected:
        return ()
    root = Path(data_dir).expanduser()
    deadline = time.monotonic() + timeout
    while True:
        missing = tuple(sorted(expected - _fresh_ocpp(root, markers)))
        if not missing:
            return ()
        if time.monotonic() >= deadline:
            return missing
        time.sleep(interval)


def _run_systemctl(action: str, service: str) -> None:
    result = subprocess.run(["systemctl", action, service], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else f"service_{action}_failed"
        raise RuntimeError(detail)


def _stop_service(service: str) -> None:
    _run_systemctl("stop", service)


def _start_service(service: str) -> None:
    _run_systemctl("start", service)


def _service_active(service: str) -> bool:
    result = subprocess.run(["systemctl", "is-active", "--quiet", service], check=False)
    return result.returncode == 0


def _prepare_redirect_receipt(state_dir: str | Path, receipt: RedirectReceipt) -> None:
    path = redirect_path(state_dir)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(receipt.to_json(), handle, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError:
        raise RuntimeError("redirect_receipt_exists") from None


def _rollback_cutover(state_dir: str | Path, old_service: str) -> None:
    errors: list[str] = []
    path = redirect_path(state_dir)
    try:
        if redirect_tools.table_exists():
            if path.exists():
                redirect_tools.remove_redirect(state_dir)
            else:
                errors.append("redirect_table_exists_without_owned_receipt")
    except (RuntimeError, ValueError, OSError) as exc:
        errors.append(f"redirect rollback failed: {exc}")
    finally:
        path.unlink(missing_ok=True)

    try:
        _start_service(old_service)
        if not _service_active(old_service):
            errors.append("old_service_not_active_after_restart")
    except RuntimeError as exc:
        errors.append(f"old service restart failed: {exc}")

    if errors:
        raise RuntimeError("; ".join(errors))


def cutover(
    *,
    data_dir: str | Path,
    state_dir: str | Path,
    old_service: str,
    timeout: float = 30.0,
    interval: float = 0.5,
) -> tuple[str, ...]:
    """Perform the controlled Path A handoff using prevalidated endpoint evidence."""
    require_root()
    receipt = load_receipt(state_dir)
    preflight = evaluate_preflight(data_dir, rollover=True)
    if not preflight.allowed:
        raise RuntimeError(preflight.reason or "handoff_preflight_blocked")
    expected = preflight.connected_chargers
    if not expected:
        raise RuntimeError("no_connected_charger_for_handoff")
    if not redirect_tools.listener_available(receipt.listen_port):
        raise RuntimeError("target_listener_unavailable")
    if redirect_tools.table_exists():
        raise RuntimeError("redirect_table_exists")
    if redirect_path(state_dir).exists():
        raise RuntimeError("redirect_receipt_exists")

    connection_baseline = connection_markers(data_dir, expected)
    ocpp_baseline = _ocpp_markers(data_dir, expected)

    final_preflight = evaluate_preflight(data_dir, rollover=True)
    if not final_preflight.allowed:
        raise RuntimeError(final_preflight.reason or "handoff_preflight_blocked")
    if final_preflight.connected_chargers != expected:
        raise RuntimeError("connected_chargers_changed_before_cutover")

    _stop_service(old_service)
    try:
        _prepare_redirect_receipt(state_dir, receipt)
        redirect_tools.apply_redirect(state_dir)

        missing_connection = wait_for_reconnect(
            data_dir,
            connection_baseline,
            timeout=timeout,
            interval=interval,
        )
        if missing_connection:
            raise RuntimeError("charger_reconnect_timeout: " + ", ".join(missing_connection))

        missing_ocpp = wait_for_fresh_ocpp(
            data_dir,
            ocpp_baseline,
            timeout=timeout,
            interval=interval,
        )
        if missing_ocpp:
            raise RuntimeError("fresh_ocpp_timeout: " + ", ".join(missing_ocpp))
        return expected
    except Exception as original:
        try:
            _rollback_cutover(state_dir, old_service)
        except Exception as rollback_error:
            raise RuntimeError(f"{original}; rollback failed: {rollback_error}") from original
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m field.handoff",
        description="Prepare and execute a validated staged OCPP handoff.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser(
        "prepare",
        help="Observe the existing charger endpoint and persist immutable pre-cutover evidence.",
    )
    prepare.add_argument("--state-dir", required=True)
    prepare.add_argument("--interface", default="eth0")
    prepare.add_argument("--listen-port", type=int, default=9000)
    prepare.add_argument("--seconds", type=float, default=15.0)
    prepare.add_argument("--capture-log")
    show = subparsers.add_parser("show", help="Print the persisted pre-cutover endpoint evidence.")
    show.add_argument("--state-dir", required=True)
    switch = subparsers.add_parser(
        "cutover",
        help="Stop the old listener, apply the prevalidated narrow redirect, and require fresh OCPP evidence.",
    )
    switch.add_argument("--data-dir", required=True)
    switch.add_argument("--state-dir", required=True)
    switch.add_argument("--old-service", required=True)
    switch.add_argument("--timeout", type=float, default=30.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            receipt = observe_existing_endpoint(
                state_dir=args.state_dir,
                interface=args.interface,
                listen_port=args.listen_port,
                seconds=args.seconds,
                capture_log=args.capture_log,
            )
            print(json.dumps(receipt.to_json(), indent=2, sort_keys=True))
            return 0
        if args.command == "show":
            print(json.dumps(load_receipt(args.state_dir).to_json(), indent=2, sort_keys=True))
            return 0
        chargers = cutover(
            data_dir=args.data_dir,
            state_dir=args.state_dir,
            old_service=args.old_service,
            timeout=args.timeout,
        )
    except (RuntimeError, ValueError, OSError) as exc:
        print(str(exc))
        return 1
    print("handoff connected: " + ", ".join(chargers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
