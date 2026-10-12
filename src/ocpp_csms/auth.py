"""HTTP Basic authentication for OCPP WebSocket upgrades.

Basic mode is only accepted over WSS. Credentials are stored as scrypt hashes.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
import re
from http import HTTPStatus
from pathlib import Path

DEFAULT_AUTH_CONFIG = Path("/etc/ocpp-csms/auth.json")


def auth_path() -> Path:
    return Path(os.environ.get("OCPP_CSMS_AUTH_CONFIG", str(DEFAULT_AUTH_CONFIG)))


def parse_limit(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)([smhdw])", value.lower())
    if match is None:
        raise ValueError("invalid limit: use 3d, 12h, 30m, etc.")
    number, unit = match.groups()
    seconds = int(number) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    if seconds > 365 * 86400:
        raise ValueError("enrollment limit exceeds 365 days")
    return seconds


def create_basic(username: str | None, password: str, path: Path | None = None, *,
                 limit: str | None = None, now: float | None = None) -> None:
    if (username is not None and (not username or "/" in username or "?" in username)) or not password:
        raise ValueError("invalid Basic authentication identity or password")
    if username is None and limit is None:
        raise ValueError("pending enrollment requires --limit")
    if username is not None and limit is not None:
        raise ValueError("--limit requires pending enrollment")
    target = path or auth_path()
    salt = secrets.token_bytes(16)
    record = {
        "mode": "basic", "username": username,
        "salt": salt.hex(),
        "hash": hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1).hex(),
    }
    if username is None:
        record["expires_at"] = (time.time() if now is None else now) + parse_limit(limit)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".auth-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(record, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_auth(path: Path | None = None) -> dict[str, str]:
    target = path or auth_path()
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"mode": "none"}
    except (OSError, ValueError) as exc:
        raise ValueError("invalid_auth_configuration") from exc
    if not isinstance(record, dict):
        raise ValueError("invalid_auth_configuration")
    if record.get("mode") == "none" and set(record) == {"mode"}:
        return {"mode": "none"}
    if record.get("mode") != "basic":
        raise ValueError("invalid_auth_configuration")
    required = {"mode", "username", "salt", "hash"}
    if record.get("username") is None:
        required.add("expires_at")
    if set(record) != required:
        raise ValueError("invalid_auth_configuration")
    try:
        username = record["username"]
        if username is not None and (not isinstance(username, str) or not username or "/" in username or "?" in username):
            raise ValueError()
        if username is None and (not isinstance(record["expires_at"], (int, float))
                             or isinstance(record["expires_at"], bool)
                             or not 0 < record["expires_at"] < 10**12):
            raise ValueError()
        if len(bytes.fromhex(record["salt"])) != 16 or len(bytes.fromhex(record["hash"])) != 64:
            raise ValueError()
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("invalid_auth_configuration") from exc
    return record


def verify_basic(config: dict, path: str, authorization: str | None, *, now: float | None = None) -> bool:
    from ocpp_csms.server import charge_point_id_from_path
    if not isinstance(authorization, str) or not authorization.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
        username, password = raw.split(":", 1)
        expected_hash = bytes.fromhex(config["hash"])
        computed = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(config["salt"]),
                                  n=16384, r=8, p=1)
        identity = charge_point_id_from_path(path)
        if not identity or not hmac.compare_digest(username, identity):
            return False
        if config["username"] is None:
            if (time.time() if now is None else now) >= config["expires_at"]:
                return False
            return hmac.compare_digest(computed, expected_hash)
        return (hmac.compare_digest(computed, expected_hash)
                and hmac.compare_digest(username, config["username"]))
    except (ValueError, UnicodeError, binascii.Error, KeyError):
        return False


def claim_basic(config: dict, config_path: Path, identity: str) -> None:
    record = dict(config)
    record["username"] = identity
    record.pop("expires_at", None)
    fd, temporary = tempfile.mkstemp(prefix=".auth-", dir=config_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(record, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, config_path)
        config.clear()
        config.update(record)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def basic_upgrade_check(config: dict, path: str, headers, *, secure: bool,
                        config_path: Path | None = None, now: float | None = None):
    if config["mode"] == "none":
        return None
    if not secure:
        return HTTPStatus.FORBIDDEN, [], b"Basic authentication requires WSS.\n"
    if verify_basic(config, path, headers.get("Authorization"), now=now):
        if config["username"] is None:
            from ocpp_csms.server import charge_point_id_from_path
            if config_path is None:
                raise ValueError("pending_enrollment_requires_persistence")
            claim_basic(config, config_path, charge_point_id_from_path(path))
        return None
    return (HTTPStatus.UNAUTHORIZED, [("WWW-Authenticate", 'Basic realm="ocpp-csms"')],
            b"Authentication required.\n")
