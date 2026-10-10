"""Durable, opt-in notifications; delivery runs outside OCPP handlers."""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from ocpp_csms.evidence.alerts import classify_event
from ocpp_csms.evidence.store import DATABASE_FILENAME
from ocpp_csms.mail import load_mail_config, send_message

LOG = logging.getLogger(__name__)
DEFAULT_CONFIG = Path("/etc/ocpp-csms/mail.toml")
LEVELS = {"info": 0, "warning": 1, "error": 2, "critical": 3}
OUTBOX = "notifications.sqlite3"


def duration_seconds(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)([smhd])", value)
    if not match:
        raise ValueError("mail.alerts.cooldown must be a positive duration (e.g. 10m)")
    return int(match[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]


def policy(path: Path) -> dict:
    import tomllib
    with path.open("rb") as handle:
        root = tomllib.load(handle).get("mail", {})
    alerts = root.get("alerts", {})
    events = root.get("events", {})
    if not isinstance(alerts, dict) or not isinstance(events, dict):
        raise ValueError("mail alerts and events must be tables")
    severity = alerts.get("minimum_severity", "warning")
    if severity not in LEVELS:
        raise ValueError("invalid mail.alerts.minimum_severity")
    cooldown = duration_seconds(alerts.get("cooldown", "10m"))
    subscriptions = {}
    for name in ("transaction_started", "transaction_stopped"):
        section = events.get(name, {})
        if not isinstance(section, dict) or type(section.get("enabled", False)) is not bool:
            raise ValueError(f"mail.events.{name}.enabled must be boolean")
        subscriptions[name] = section.get("enabled", False)
    if type(alerts.get("enabled", False)) is not bool:
        raise ValueError("mail.alerts.enabled must be boolean")
    return {"alerts": alerts.get("enabled", False), "minimum": severity,
            "cooldown": cooldown, "subscriptions": subscriptions}


def _outbox(path: Path) -> sqlite3.Connection:
    path.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path / OUTBOX, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS cursors (source TEXT PRIMARY KEY, last_id INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            identity TEXT NOT NULL UNIQUE,
            recipient TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            kind TEXT NOT NULL,
            cooldown_key TEXT,
            created_at TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            due_at TEXT NOT NULL,
            sent_at TEXT,
            last_error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_messages_pending ON messages(state, due_at);
    """)
    return db


def enqueue(db: sqlite3.Connection, *, identity: str, recipient: str, subject: str,
            body: str, kind: str, at: datetime, cooldown_key: str | None = None,
            cooldown: int = 0) -> None:
    now = at.isoformat()
    if cooldown_key:
        latest = db.execute(
            "SELECT created_at FROM messages WHERE cooldown_key=? ORDER BY id DESC LIMIT 1",
            (cooldown_key,),
        ).fetchone()
        if latest and at - datetime.fromisoformat(latest[0]) < timedelta(seconds=cooldown):
            return
    db.execute("""INSERT OR IGNORE INTO messages
        (identity, recipient, subject, body, kind, cooldown_key, created_at, due_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (identity, recipient, subject, body, kind, cooldown_key, now, now))


def collect(data_dir: Path, config_path: Path = DEFAULT_CONFIG) -> None:
    """Read persisted evidence and enqueue without making SMTP network requests.

    A new installation begins from its current evidence high-water marks,
    preventing an unsolicited historical mail flood.
    """
    config = load_mail_config(config_path)
    if config is None or not config.enabled:
        return
    rules = policy(config_path)
    evidence = data_dir / DATABASE_FILENAME
    if not evidence.exists():
        return
    with sqlite3.connect(f"file:{evidence}?mode=ro", uri=True) as src, _outbox(data_dir) as dst:
        src.row_factory = sqlite3.Row
        sources = {
            "ocpp": ("events", "id", """SELECT id, received_at AS occurred_at, charger_id,
                       'ocpp' AS kind, action, direction, transaction_id, id_tag,
                       payload_json AS payload FROM events WHERE id>? ORDER BY id LIMIT 500"""),
            "runtime": ("runtime_events", "id", """SELECT id, occurred_at, charger_id,
                         'runtime' AS kind, event AS action, NULL AS direction,
                         NULL AS transaction_id, NULL AS id_tag, details_json AS payload
                         FROM runtime_events WHERE id>? ORDER BY id LIMIT 500"""),
            "transactions": ("transactions", "transaction_id", ""),
        }
        now = datetime.now(timezone.utc)
        for source, (table, key, query) in sources.items():
            cursor = dst.execute("SELECT last_id FROM cursors WHERE source=?", (source,)).fetchone()
            if cursor is None:
                top = src.execute(f"SELECT COALESCE(MAX({key}), 0) FROM {table}").fetchone()[0]
                dst.execute("INSERT INTO cursors VALUES (?,?)", (source, top))
                continue
            if source == "transactions":
                # Transaction IDs are not guaranteed to be insertion ordered.
                # Transaction notifications are derived from OCPP source IDs below.
                continue
            last_id = int(cursor[0])
            while True:
                rows = src.execute(query, (last_id,)).fetchall()
                for row in rows:
                    record = dict(row)
                    at = datetime.fromisoformat(record["occurred_at"].replace("Z", "+00:00"))
                    alert = classify_event(record)
                    if alert and rules["alerts"] and LEVELS[alert["severity"]] >= LEVELS[rules["minimum"]]:
                        for recipient in config.recipients:
                            key = f"{alert['category']}:{record['charger_id']}:{alert.get('connector_id')}"
                            enqueue(dst, identity=f"{source}:{record['id']}:{recipient}", recipient=recipient,
                                    subject=f"[OCPP-CSMS {alert['severity'].upper()}] {alert['message']}",
                                    body=json.dumps(alert, indent=2, default=str), kind="alert", at=at,
                                    cooldown_key=f"{recipient}:{key}", cooldown=rules["cooldown"])
                    if source == "ocpp" and record["action"] in ("StartTransaction", "StopTransaction"):
                        action = record["action"]
                        name = "transaction_started" if action == "StartTransaction" else "transaction_stopped"
                        if not rules["subscriptions"][name] or record["direction"] != "in":
                            continue
                        txn = record["transaction_id"]
                        if txn is None or (action == "StartTransaction" and int(txn) <= 0):
                            continue
                        # Persisted transaction state, not merely a received request.
                        state = src.execute("SELECT charger_id, connector_id, id_tag, started_at, stopped_at, meter_start, meter_stop FROM transactions WHERE transaction_id=?", (txn,)).fetchone()
                        if state is None or (action == "StopTransaction" and state["stopped_at"] is None):
                            continue
                        for recipient in config.recipients:
                            kind = name
                            identity = f"{kind}:{state['charger_id']}:{txn}:{recipient}"
                            body = "\n".join(f"{label}: {state[label]}" for label in state.keys())
                            enqueue(dst, identity=identity, recipient=recipient,
                                    subject=f"[OCPP-CSMS] {kind.replace('_', ' ').title()} #{txn}",
                                    body=body, kind=kind, at=at)
                    last_id = record["id"]
                dst.execute("UPDATE cursors SET last_id=? WHERE source=?", (last_id, source))
                if len(rows) < 500:
                    break


def deliver(data_dir: Path, config_path: Path = DEFAULT_CONFIG, *, batch: int = 20) -> None:
    config = load_mail_config(config_path)
    if config is None or not config.enabled:
        return
    with _outbox(data_dir) as db:
        now = datetime.now(timezone.utc).isoformat()
        rows = db.execute("""SELECT * FROM messages WHERE state='pending' AND due_at<=?
                             ORDER BY id LIMIT ?""", (now, batch)).fetchall()
        for row in rows:
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = config.sender, row["recipient"], row["subject"]
            msg.set_content(row["body"])
            try:
                send_message(config, msg)
            except Exception as exc:
                delay = min(3600, 30 * 2 ** min(row["attempts"], 7))
                due = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                db.execute("""UPDATE messages SET attempts=attempts+1, due_at=?, last_error=?
                              WHERE id=?""", (due, type(exc).__name__, row["id"]))
                LOG.warning("Mail send failed (%s), retry scheduled", type(exc).__name__)
            else:
                db.execute("""UPDATE messages SET state='sent', attempts=attempts+1,
                              sent_at=?, last_error=NULL WHERE id=?""",
                           (datetime.now(timezone.utc).isoformat(), row["id"]))


def history(data_dir: Path, *, failed: bool = False) -> list[dict]:
    if not (data_dir / OUTBOX).exists():
        return []
    with _outbox(data_dir) as db:
        clause = "WHERE attempts>0 AND state='pending'" if failed else ""
        rows = db.execute(f"""SELECT id, recipient, kind, state, attempts, created_at,
                              sent_at, last_error FROM messages {clause}
                              ORDER BY id DESC LIMIT 100""").fetchall()
        return [dict(row) for row in rows]


async def notification_loop(data_dir: Path, config_path: Path = DEFAULT_CONFIG) -> None:
    import asyncio
    while True:
        try:
            await asyncio.to_thread(collect, data_dir, config_path)
            await asyncio.to_thread(deliver, data_dir, config_path)
        except Exception:
            LOG.exception("Notification worker cycle failed")
        await asyncio.sleep(5)
