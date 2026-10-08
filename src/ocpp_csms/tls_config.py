"""Persistent TLS configuration and readiness checks; no listener changes."""
from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_CONFIG = Path("/etc/ocpp-csms/tls.json")


@dataclass(frozen=True)
class TLSConfig:
    cert: str
    key: str
    hostname: str
    port: int = 9443
    enabled: bool = False


def validate_fields(config: TLSConfig) -> None:
    if not config.hostname or len(config.hostname) > 253 or not re.fullmatch(r"[A-Za-z0-9*._-]+", config.hostname):
        raise ValueError("invalid_tls_hostname")
    if not isinstance(config.port, int) or isinstance(config.port, bool) or not 1 <= config.port <= 65535:
        raise ValueError("invalid_tls_port")
    if not isinstance(config.enabled, bool):
        raise ValueError("invalid_tls_enabled")
    for value in (config.cert, config.key):
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError("tls_paths_must_be_absolute")


def read_config(path: str | Path = DEFAULT_CONFIG) -> TLSConfig | None:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_tls_configuration") from exc
    if not isinstance(raw, dict) or set(raw) != set(TLSConfig.__dataclass_fields__):
        raise ValueError("invalid_tls_configuration")
    try:
        config = TLSConfig(**raw)
        validate_fields(config)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_tls_configuration") from exc
    return config


def write_config(config: TLSConfig, path: str | Path = DEFAULT_CONFIG) -> None:
    validate_fields(config)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tls-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(asdict(config), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def check_config(config: TLSConfig, *, ws_port: int = 9000) -> dict[str, object]:
    """Read-only local readiness; does not claim charger trust."""
    validate_fields(config)
    errors: list[str] = []
    if config.port == ws_port:
        errors.append("tls_port_conflicts_with_ws")
    for name, value in (("cert", config.cert), ("key", config.key)):
        path = Path(value)
        if not path.is_file():
            errors.append(f"{name}_not_found")
        elif not os.access(path, os.R_OK):
            errors.append(f"{name}_not_readable")
    certificate = Path(config.cert)
    key = Path(config.key)
    if not errors:
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(config.cert, config.key)
        except (OSError, ssl.SSLError) as exc:
            errors.append(f"tls_certificate_key_invalid: {exc.__class__.__name__}")

        try:
            validity = subprocess.run(
                ["openssl", "x509", "-in", str(certificate), "-noout", "-checkend", "0"],
                capture_output=True, text=True, check=False,
            )
            if validity.returncode != 0:
                errors.append("tls_certificate_expired_or_invalid")
            names = subprocess.run(
                ["openssl", "x509", "-in", str(certificate), "-noout", "-ext", "subjectAltName"],
                capture_output=True, text=True, check=False,
            )
            if names.returncode != 0:
                errors.append("tls_certificate_san_unavailable")
            else:
                dns_names = re.findall(r"DNS:([^,\s]+)", names.stdout)
                hostname = config.hostname.lower()
                def matches(pattern: str) -> bool:
                    pattern = pattern.lower()
                    return pattern == hostname or (
                        pattern.startswith("*.") and
                        hostname.endswith(pattern[1:]) and
                        hostname.count(".") == pattern.count(".")
                    )
                if not any(matches(name) for name in dns_names):
                    errors.append("tls_hostname_not_in_san")
        except FileNotFoundError:
            errors.append("openssl_not_found")
    return {
        "ready": not errors,
        "errors": errors,
        "charger_trust": "unknown",
        "listener": "not_implemented",
        "hostname": config.hostname,
        "port": config.port,
        "enabled": config.enabled,
    }


def status(path: str | Path = DEFAULT_CONFIG) -> dict[str, object]:
    config = read_config(path)
    if config is None:
        return {"configured": False, "enabled": False, "listener": "not_implemented"}
    return {
        "configured": True, "enabled": config.enabled, "hostname": config.hostname,
        "port": config.port, "cert": config.cert, "key": "configured",
        "listener": "not_implemented", "charger_trust": "unknown",
    }
