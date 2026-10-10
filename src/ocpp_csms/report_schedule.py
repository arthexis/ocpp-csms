"""Idempotent scheduled operational report enqueueing.

Designed for a systemd timer, not the OCPP message-processing loop.
"""
from __future__ import annotations

import sqlite3
import tomllib
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ocpp_csms.mail import load_mail_config
from ocpp_csms.notifications import _outbox
from ocpp_csms.reports import build_report, format_report


def _jobs(config_path: Path):
    with config_path.open("rb") as handle:
        mail = tomllib.load(handle).get("mail", {})
    reports = mail.get("reports", {})
    if not isinstance(reports, dict):
        raise ValueError("mail.reports must be a table")
    for cadence in ("daily", "weekly"):
        settings = reports.get(cadence, {})
        if not isinstance(settings, dict):
            raise ValueError(f"mail.reports.{cadence} must be a table")
        enabled = settings.get("enabled", False)
        if type(enabled) is not bool:
            raise ValueError(f"mail.reports.{cadence}.enabled must be boolean")
        if not enabled:
            continue
        zone_name = settings.get("timezone", "UTC")
        try:
            zone = ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, TypeError) as exc:
            raise ValueError(f"invalid report timezone: {zone_name}") from exc
        value = settings.get("time", "08:00")
        try:
            hour, minute = [int(x) for x in value.split(":")]
            if len(value) != 5 or not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError()
        except (AttributeError, ValueError):
            raise ValueError(f"invalid mail.reports.{cadence}.time") from None
        day = settings.get("weekday", "monday")
        if cadence == "weekly" and day not in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
            raise ValueError("invalid weekly report weekday")
        yield cadence, zone_name, zone, time(hour, minute), day


def enqueue_scheduled(data_dir: Path, config_path: Path, *,
                      now: datetime | None = None) -> int:
    """Queue each completed local calendar window once, per recipient.

    Uses a unique outbox identity to prevent timer overlap and repeat sends.
    An SMTP delivery failure leaves the queued message for normal retry.
    """
    config = load_mail_config(config_path)
    if config is None or not config.enabled:
        return 0
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("current time must include a timezone")
    count = 0
    with _outbox(Path(data_dir)) as db:
        for cadence, name, zone, send_time, weekday in _jobs(config_path):
            today = now.astimezone(zone).date()
            scheduled_date = today
            if now.astimezone(zone).timetz().replace(tzinfo=None) < send_time:
                scheduled_date -= timedelta(days=1)
            if cadence == "weekly":
                target = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday").index(weekday)
                scheduled_date -= timedelta(days=(scheduled_date.weekday() - target) % 7)
            end_local = datetime.combine(scheduled_date, time.min, zone)
            start_local = end_local - timedelta(days=1 if cadence == "daily" else 7)
            start = start_local.astimezone(timezone.utc)
            end = end_local.astimezone(timezone.utc)
            report_id = f"report:{cadence}:{name}:{start_local.date()}:{end_local.date()}"
            if all(db.execute("SELECT 1 FROM messages WHERE identity=?", (f"{report_id}:{address}",)).fetchone()
                   for address in config.recipients):
                continue
            report = build_report(data_dir, since=start, until=end)
            body = format_report(report)
            for address in config.recipients:
                identity = f"{report_id}:{address}"
                cursor = db.execute("""INSERT OR IGNORE INTO messages
                    (identity, recipient, subject, body, kind, created_at, due_at)
                    VALUES (?, ?, ?, ?, 'report', ?, ?)""", (
                        identity, address,
                        f"[OCPP-CSMS] {cadence.title()} report {start_local.date()} to {end_local.date()}",
                        body, now.astimezone(timezone.utc).isoformat(),
                        now.astimezone(timezone.utc).isoformat(),
                    ))
                count += cursor.rowcount
    return count
