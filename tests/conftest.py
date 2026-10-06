from __future__ import annotations

import sqlite3

import pytest

import ocpp_csms.cli.profile as profile_cli
from ocpp_csms.cli import build_parser
from ocpp_csms.schema import DATABASE_FILENAME


class ProfileControlStub:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.response: dict[str, object] = {"ok": True, "response": {"status": "Accepted"}}

    async def __call__(self, data_dir, request):
        self.calls.append((data_dir, request))
        return self.response


@pytest.fixture
def cli_parser():
    parser, _ = build_parser()
    return parser


@pytest.fixture
def profile_control(monkeypatch):
    stub = ProfileControlStub()
    monkeypatch.setattr(profile_cli, "send_control", stub)
    return stub


@pytest.fixture
def schema_one(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at TEXT NOT NULL,
                charger_id TEXT NOT NULL,
                action TEXT NOT NULL,
                direction TEXT NOT NULL,
                transaction_id INTEGER,
                id_tag TEXT,
                charger_timestamp TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE runtime_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                event TEXT NOT NULL,
                charger_id TEXT,
                details_json TEXT
            );
            PRAGMA user_version = 1;
            """
        )
        connection.execute(
            "INSERT INTO runtime_events (occurred_at, event, charger_id) VALUES (?, ?, ?)",
            ("2026-10-04T00:00:00Z", "legacy_evidence", "charger-a"),
        )
    return path


@pytest.fixture
def record_runtime_event(tmp_path):
    def record(event: str, charger_id: str, occurred_at: str = "2026-10-04T16:00:00Z") -> int:
        with sqlite3.connect(tmp_path / DATABASE_FILENAME) as connection:
            cursor = connection.execute(
                "INSERT INTO runtime_events (occurred_at, event, charger_id) VALUES (?, ?, ?)",
                (occurred_at, event, charger_id),
            )
            return int(cursor.lastrowid)

    return record
