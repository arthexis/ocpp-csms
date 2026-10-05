from __future__ import annotations

import json
import os
from pathlib import Path

from ocpp_discover import handoff, persistence, redirect
from ocpp_discover.redirect import RedirectReceipt
from ocpp_csms.install_cutover import connection_markers, wait_for_reconnect
from ocpp_csms.install_preflight import evaluate_preflight


def _charger_ids(receipt: RedirectReceipt) -> tuple[str, ...]:
    ids = {request.path.rstrip("/").rsplit("/", 1)[-1] for request in receipt.requests}
    ids.discard("")
    if not ids:
        raise RuntimeError("discovered_receipt_missing_charger_identity")
    return tuple(sorted(ids))


def _replace_live(receipt: RedirectReceipt) -> None:
    """Replace only Discover's owned live table in one nft transaction."""
    redirect.require_root()
    handoff.validate_discovered_redirect(receipt)
    if not redirect.listener_available(receipt.listen_port):
        raise RuntimeError("listener_unavailable")
    redirect.validate_ruleset(receipt)
    prefix = "delete table ip ocpp_field_redirect\n" if redirect.table_exists() else ""
    result = redirect._run_nft(["nft", "-f", "-"], input_text=prefix + redirect.render_ruleset(receipt))
    if result.returncode != 0:
        raise redirect._nft_error(result, "nft_reconciliation_failed")


def _replace_discovered(state_dir: str | Path, receipt: RedirectReceipt) -> None:
    """Atomically replace durable evidence only after the candidate has been proven."""
    payload = handoff._discovered_payload(receipt)
    path = handoff.discovered_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.reconcile-{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def reconcile(
    *,
    data_dir: str | Path,
    persistent_dir: str | Path,
    expected: RedirectReceipt,
    candidate: RedirectReceipt,
    timeout: float = 30.0,
    ruleset_path: str | Path = persistence.DEFAULT_RULESET_PATH,
) -> RedirectReceipt:
    """Replace a contradicted adaptation, proving the candidate before durable commit."""
    handoff.validate_discovered_redirect(expected)
    handoff.validate_discovered_redirect(candidate)
    if expected == candidate:
        return expected

    preflight = evaluate_preflight(data_dir, rollover=True)
    if not preflight.allowed:
        raise RuntimeError(preflight.reason or "reconciliation_preflight_failed")

    chargers = _charger_ids(candidate)
    connection_baseline = connection_markers(data_dir, chargers)
    ocpp_baseline = handoff._ocpp_markers(data_dir, chargers)
    ruleset = Path(ruleset_path)
    old_ruleset = ruleset.read_text(encoding="utf-8") if ruleset.exists() else persistence.EMPTY_RULESET
    old_mode = (ruleset.stat().st_mode & 0o777) if ruleset.exists() else 0o600

    live_changed = False
    durable_changed = False
    try:
        _replace_live(candidate)
        live_changed = True
        missing = wait_for_reconnect(data_dir, connection_baseline, timeout=timeout)
        if missing:
            raise RuntimeError("reconciliation_reconnect_timeout: " + ", ".join(missing))
        missing_ocpp = handoff.wait_for_fresh_ocpp(data_dir, ocpp_baseline, timeout=timeout)
        if missing_ocpp:
            raise RuntimeError("reconciliation_ocpp_timeout: " + ", ".join(missing_ocpp))

        new_ruleset = persistence.render_persistent_ruleset(candidate)
        persistence.check_nftables_text(new_ruleset)
        persistence.replace_ruleset(new_ruleset, ruleset_path=ruleset)
        durable_changed = True
        _replace_discovered(persistent_dir, candidate)
        return candidate
    except Exception as exc:
        rollback_errors: list[str] = []
        if durable_changed:
            try:
                persistence.replace_ruleset(old_ruleset, ruleset_path=ruleset, mode=old_mode)
            except Exception as rollback_exc:
                rollback_errors.append(f"persistent rollback failed: {rollback_exc}")
        if live_changed:
            try:
                _replace_live(expected)
            except Exception as rollback_exc:
                rollback_errors.append(f"live rollback failed: {rollback_exc}")
        if rollback_errors:
            raise RuntimeError(f"{exc}; " + "; ".join(rollback_errors)) from exc
        raise
