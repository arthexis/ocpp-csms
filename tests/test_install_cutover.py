import json
import sqlite3

import pytest

from ocpp_csms import install_cutover
from ocpp_csms.schema import DATABASE_FILENAME, create_current_schema


def test_schema_action_create_for_missing_database(tmp_path):
    assert install_cutover.schema_action(tmp_path) == "create"


def test_schema_action_current_for_current_database(tmp_path):
    create_current_schema(tmp_path)
    assert install_cutover.schema_action(tmp_path) == "current"


def test_schema_action_upgrade_for_known_old_database(tmp_path):
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
    assert install_cutover.schema_action(tmp_path) == "upgrade"


def test_connection_markers_capture_pre_cutover_event_ids(tmp_path):
    create_current_schema(tmp_path)
    path = tmp_path / DATABASE_FILENAME
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO runtime_events (occurred_at, event, charger_id) VALUES ('a', 'charger_connected', 'a')"
        )
        connection.execute(
            "INSERT INTO runtime_events (occurred_at, event, charger_id) VALUES ('b', 'charger_connected', 'b')"
        )
        connection.execute(
            "INSERT INTO runtime_events (occurred_at, event, charger_id) VALUES ('c', 'charger_connected', 'a')"
        )
    assert install_cutover.connection_markers(tmp_path, ("a", "b")) == {"a": 3, "b": 2}


def test_wait_for_reconnect_requires_fresh_connection_event(tmp_path, monkeypatch):
    create_current_schema(tmp_path)
    path = tmp_path / DATABASE_FILENAME
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO runtime_events (occurred_at, event, charger_id) VALUES ('a', 'charger_connected', 'a')"
        )
    markers = {"a": 1}
    monkeypatch.setattr(install_cutover.time, "sleep", lambda value: None)
    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(install_cutover.time, "monotonic", lambda: next(ticks))

    assert install_cutover.wait_for_reconnect(tmp_path, markers, timeout=1.0) == ("a",)


def test_wait_for_reconnect_accepts_new_connected_event(tmp_path):
    create_current_schema(tmp_path)
    path = tmp_path / DATABASE_FILENAME
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO runtime_events (occurred_at, event, charger_id) VALUES ('a', 'charger_connected', 'a')"
        )
    markers = {"a": 1}
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO runtime_events (occurred_at, event, charger_id) VALUES ('b', 'charger_disconnected', 'a')"
        )
        connection.execute(
            "INSERT INTO runtime_events (occurred_at, event, charger_id) VALUES ('c', 'charger_connected', 'a')"
        )

    assert install_cutover.wait_for_reconnect(tmp_path, markers, timeout=0) == ()


def test_capture_baseline_cli_uses_preflight_connected_set(tmp_path):
    create_current_schema(tmp_path)
    path = tmp_path / DATABASE_FILENAME
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO runtime_events (occurred_at, event, charger_id) VALUES ('a', 'charger_connected', 'charger-a')"
        )
    preflight = tmp_path / "preflight.json"
    preflight.write_text(json.dumps({"connected_chargers": ["charger-a"]}), encoding="utf-8")
    baseline = tmp_path / "baseline.json"

    assert install_cutover.main([
        "capture-baseline",
        "--data-dir", str(tmp_path),
        "--preflight-json", str(preflight),
        "--output", str(baseline),
    ]) == 0
    assert json.loads(baseline.read_text(encoding="utf-8")) == {"charger-a": 1}
