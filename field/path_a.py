from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from field.redirect import RedirectReceipt, receipt_from_json, validate_receipt

_STATE_FILENAME = "path-a.json"
_STATE_KIND = "ocpp-path-a"
_STATE_VERSION = 1


def state_path(state_dir: str | Path) -> Path:
    return Path(state_dir).expanduser() / _STATE_FILENAME


def _canonical_receipt(receipt: RedirectReceipt) -> bytes:
    return json.dumps(receipt.to_json(), sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(receipt: RedirectReceipt) -> str:
    return hashlib.sha256(_canonical_receipt(receipt)).hexdigest()


def validate_path_a_receipt(receipt: RedirectReceipt) -> None:
    """Require the exact unambiguous receipt shape authorized for durable Path A."""
    validate_receipt(receipt)
    if len(receipt.destination_ips) != 1:
        raise ValueError("path_a_requires_single_destination")
    if not receipt.requests:
        raise ValueError("path_a_requires_websocket_evidence")
    destination = receipt.destination_ips[0]
    for request in receipt.requests:
        if request.destination_ip != destination:
            raise ValueError("path_a_destination_evidence_mismatch")
        if not request.host.strip():
            raise ValueError("path_a_missing_host_evidence")
        if not request.path.startswith("/"):
            raise ValueError("path_a_invalid_path_evidence")


def persist_receipt(state_dir: str | Path, receipt: RedirectReceipt) -> Path:
    """Atomically persist only a validated, exact Path A adaptation receipt."""
    validate_path_a_receipt(receipt)
    path = state_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": _STATE_KIND,
        "version": _STATE_VERSION,
        "receipt": receipt.to_json(),
        "sha256": _digest(receipt),
    }
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return path


def load_receipt(state_dir: str | Path) -> RedirectReceipt:
    path = state_path(state_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError("path_a_receipt_not_found") from None
    except json.JSONDecodeError:
        raise RuntimeError("invalid_path_a_receipt") from None

    if not isinstance(payload, dict):
        raise RuntimeError("invalid_path_a_receipt")
    if set(payload) != {"kind", "version", "receipt", "sha256"}:
        raise RuntimeError("invalid_path_a_receipt")
    if payload["kind"] != _STATE_KIND or payload["version"] != _STATE_VERSION:
        raise RuntimeError("incompatible_path_a_receipt")
    try:
        receipt = receipt_from_json(payload["receipt"])
        validate_path_a_receipt(receipt)
    except (TypeError, ValueError):
        raise RuntimeError("invalid_path_a_receipt") from None
    digest = payload["sha256"]
    if not isinstance(digest, str) or digest != _digest(receipt):
        raise RuntimeError("path_a_receipt_integrity_mismatch")
    return receipt


def remove_receipt(state_dir: str | Path) -> bool:
    """Remove only the OCPP-owned durable Path A receipt, if present."""
    path = state_path(state_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
