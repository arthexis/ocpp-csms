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
from http import HTTPStatus
from pathlib import Path

DEFAULT_AUTH_CONFIG = Path("/etc/ocpp-csms/auth.json")


def auth_path() -> Path:
    return Path(os.environ.get("OCPP_CSMS_AUTH_CONFIG", str(DEFAULT_AUTH_CONFIG)))


def create_basic(username: str, password: str, path: Path | None = None) -> None:
    if not username or "/" in username or "?" in username or not password:
        raise ValueError("invalid Basic authentication identity or password")
    target = path or auth_path()
    salt = secrets.token_bytes(16)
    record = {
        "mode": "basic", "username": username,
        "salt": salt.hex(),
        "hash": hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1).hex(),
    }
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
    if record.get("mode") != "basic" or set(record) != {"mode", "username", "salt", "hash"}:
        raise ValueError("invalid_auth_configuration")
    try:
        if not isinstance(record["username"], str) or not record["username"] or "/" in record["username"]:
            raise ValueError()
        if len(bytes.fromhex(record["salt"])) != 16 or len(bytes.fromhex(record["hash"])) != 64:
            raise ValueError()
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("invalid_auth_configuration") from exc
    return record


def verify_basic(config: dict[str, str], path: str, authorization: str | None) -> bool:
    from ocpp_csms.server import charge_point_id_from_path
    if not isinstance(authorization, str) or not authorization.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
        username, password = raw.split(":", 1)
        expected_hash = bytes.fromhex(config["hash"])
        computed = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(config["salt"]),
                                  n=16384, r=8, p=1)
        return (hmac.compare_digest(computed, expected_hash)
                and hmac.compare_digest(username, config["username"])
                and hmac.compare_digest(username, charge_point_id_from_path(path)))
    except (ValueError, UnicodeError, binascii.Error, KeyError):
        return False


def basic_upgrade_check(config: dict[str, str], path: str, headers, *, secure: bool):
    if config["mode"] == "none":
        return None
    if not secure:
        return HTTPStatus.FORBIDDEN, [], b"Basic authentication requires WSS.\n"
    if verify_basic(config, path, headers.get("Authorization")):
        return None
    return (HTTPStatus.UNAUTHORIZED, [("WWW-Authenticate", 'Basic realm="ocpp-csms"')],
            b"Authentication required.\n")
