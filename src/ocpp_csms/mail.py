"""Optional SMTP transport, isolated from the OCPP servicing path."""
from __future__ import annotations

import os
import smtplib
import ssl
import tomllib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MailConfig:
    enabled: bool
    sender: str
    recipients: tuple[str, ...]
    host: str
    port: int
    starttls: bool
    ssl_enabled: bool
    username: str
    password_env: str
    timeout: float


def _table(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a TOML table")
    return value


def load_mail_config(path: str | Path) -> MailConfig | None:
    """Read and validate mail settings; no network or credential access."""
    path = Path(path).expanduser()
    if not path.exists():
        return None
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    mail = _table(data.get("mail"), "mail")
    smtp = _table(mail.get("smtp", {}), "mail.smtp")
    enabled = mail.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("mail.enabled must be boolean")
    recipients = mail.get("to", [])
    if not isinstance(recipients, list) or any(not isinstance(v, str) or not v.strip() for v in recipients):
        raise ValueError("mail.to must be a list of recipient email addresses")
    starttls = smtp.get("starttls", True)
    ssl_enabled = smtp.get("ssl", False)
    if not isinstance(starttls, bool) or not isinstance(ssl_enabled, bool):
        raise ValueError("mail.smtp TLS flags must be boolean")
    if starttls and ssl_enabled:
        raise ValueError("select STARTTLS or implicit TLS, not both")
    if enabled and not (starttls or ssl_enabled):
        raise ValueError("authenticated mail requires TLS")
    port = smtp.get("port", 587)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("mail.smtp.port must be between 1 and 65535")
    timeout = smtp.get("timeout", 10)
    if type(timeout) not in (int, float) or not 0 < timeout <= 120:
        raise ValueError("mail.smtp.timeout must be between 0 and 120 seconds")
    result = MailConfig(
        enabled=enabled, sender=str(mail.get("from", "")).strip(),
        recipients=tuple(recipients), host=str(smtp.get("host", "")).strip(),
        port=port, starttls=starttls, ssl_enabled=ssl_enabled,
        username=str(smtp.get("username", "")).strip(),
        password_env=str(smtp.get("password_env", "")).strip(),
        timeout=float(timeout),
    )
    if enabled and (not result.sender or not result.recipients or not result.host):
        raise ValueError("enabled mail requires from, to, and smtp.host")
    if enabled and result.username and not result.password_env:
        raise ValueError("SMTP username requires password_env")
    if enabled and not (result.starttls or result.ssl_enabled):
        raise ValueError("mail transport must use TLS")
    # Validate future policy tables early, without implementing their delivery here.
    for section in ("alerts", "events", "reports"):
        if section in mail:
            _table(mail[section], f"mail.{section}")
    return result


def mail_status(config: MailConfig | None) -> dict[str, Any]:
    if config is None:
        return {"configured": False, "enabled": False}
    return {
        "configured": True, "enabled": config.enabled,
        "from": config.sender, "to": list(config.recipients),
        "smtp_host": config.host, "smtp_port": config.port,
        "tls": "starttls" if config.starttls else "implicit" if config.ssl_enabled else "none",
        "username": config.username or None,
        "credentials_available": bool(config.password_env and os.environ.get(config.password_env)),
    }


def send_message(config: MailConfig, msg: EmailMessage) -> None:
    """Deliver one prepared message over authenticated, verified TLS."""
    if not config.enabled or not (config.starttls or config.ssl_enabled):
        raise ValueError("mail disabled or SMTP TLS unavailable")
    if not config.host or not config.sender or not config.recipients:
        raise ValueError("mail is not completely configured")
    password = os.environ.get(config.password_env) if config.password_env else None
    if config.username and not password:
        raise ValueError("SMTP credential environment variable is unavailable")
    context = ssl.create_default_context()
    connection_type = smtplib.SMTP_SSL if config.ssl_enabled else smtplib.SMTP
    with connection_type(config.host, config.port, timeout=config.timeout,
                         **({"context": context} if config.ssl_enabled else {})) as smtp:
        if config.starttls:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        if config.username:
            smtp.login(config.username, password)
        smtp.send_message(msg)


def send_test(config: MailConfig) -> None:
    """Explicit operator test; never invoked from OCPP handlers."""
    msg = EmailMessage()
    msg["From"] = config.sender
    msg["To"] = ", ".join(config.recipients)
    msg["Subject"] = "OCPP-CSMS SMTP test"
    msg.set_content("This test confirms the SMTP server accepted a message from OCPP-CSMS.")
    send_message(config, msg)
