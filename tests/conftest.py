from __future__ import annotations

import sqlite3

import pytest

from ocpp_csms.schema import DATABASE_FILENAME


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


@pytest.fixture
def cli_parser():
    """Real production CLI parser, shared by command-level tests."""
    from ocpp_csms.cli import build_parser
    return build_parser()[0]
