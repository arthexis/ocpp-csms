import json
import sqlite3

from ocpp_csms import install_preflight
from ocpp_csms.schema import DATABASE_FILENAME, create_current_schema


def _record_connection(tmp_path, charger_id="charger-a"):
    create_current_schema(tmp_path)
    with sqlite3.connect(tmp_path / DATABASE_FILENAME) as connection:
        connection.execute(
            "INSERT INTO runtime_events (occurred_at, event, charger_id) VALUES (?, 'charger_connected', ?)",
            ("2026-10-04T16:00:00Z", charger_id),
        )


def _record_active_archive(tmp_path, charger_id="charger-a", transaction_id=7):
    folder = tmp_path / "transactions" / "2026-10-04"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{charger_id}-{transaction_id}.json").write_text(
        json.dumps(
            {
                "transaction_id": transaction_id,
                "charge_point_id": charger_id,
                "status": "open",
                "start": {
                    "connector_id": 1,
                    "id_tag": "CARD",
                    "meter_start": 100,
                    "timestamp": "2026-10-04T16:01:00Z",
                },
            }
        ),
        encoding="utf-8",
    )


def _record_active_database(tmp_path, charger_id="charger-a", transaction_id=7):
    with sqlite3.connect(tmp_path / DATABASE_FILENAME) as connection:
        connection.execute(
            """
            INSERT INTO transactions (
                transaction_id, charger_id, connector_id, id_tag,
                meter_start, started_at, start_received_at, state, last_activity_at
            ) VALUES (?, ?, 1, 'CARD', 100, ?, ?, 'open', ?)
            """,
            (
                transaction_id,
                charger_id,
                "2026-10-04T16:01:00Z",
                "2026-10-04T16:01:00Z",
                "2026-10-04T16:01:00Z",
            ),
        )


def test_new_install_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(install_preflight, "process_is_running", lambda data_dir: False)

    result = install_preflight.evaluate_preflight(tmp_path)

    assert result.allowed is True
    assert result.connected_chargers == ()
    assert result.active_chargers == ()


def test_idle_connected_charger_allows_safe_handoff(tmp_path, monkeypatch):
    _record_connection(tmp_path)
    monkeypatch.setattr(install_preflight, "process_is_running", lambda data_dir: True)

    result = install_preflight.evaluate_preflight(tmp_path)

    assert result.allowed is True
    assert result.connected_chargers == ("charger-a",)
    assert result.active_chargers == ()


def test_active_transaction_blocks_service_handoff(tmp_path, monkeypatch):
    _record_connection(tmp_path)
    _record_active_archive(tmp_path)
    _record_active_database(tmp_path)
    monkeypatch.setattr(install_preflight, "process_is_running", lambda data_dir: True)

    result = install_preflight.evaluate_preflight(tmp_path)

    assert result.allowed is False
    assert result.active_chargers == ("charger-a",)
    assert "service handoff is blocked" in result.reason


def test_disagreement_between_sqlite_and_archive_blocks_install(tmp_path, monkeypatch):
    _record_connection(tmp_path)
    _record_active_database(tmp_path)
    monkeypatch.setattr(install_preflight, "process_is_running", lambda data_dir: True)

    result = install_preflight.evaluate_preflight(tmp_path)

    assert result.allowed is False
    assert result.active_chargers == ("charger-a",)
    assert "disagrees" in result.reason


def test_preflight_ignores_stale_connected_event_when_server_is_stopped(tmp_path, monkeypatch):
    _record_connection(tmp_path)
    monkeypatch.setattr(install_preflight, "process_is_running", lambda data_dir: False)

    result = install_preflight.evaluate_preflight(tmp_path)

    assert result.allowed is True
    assert result.connected_chargers == ()


def test_unversioned_existing_database_is_blocked(tmp_path, monkeypatch):
    (tmp_path / DATABASE_FILENAME).touch()
    monkeypatch.setattr(install_preflight, "process_is_running", lambda data_dir: False)

    result = install_preflight.evaluate_preflight(tmp_path)

    assert result.allowed is False
    assert "unversioned" in result.reason


def test_newer_database_schema_is_blocked(tmp_path, monkeypatch):
    with sqlite3.connect(tmp_path / DATABASE_FILENAME) as connection:
        connection.execute("PRAGMA user_version = 99")
    monkeypatch.setattr(install_preflight, "process_is_running", lambda data_dir: False)

    result = install_preflight.evaluate_preflight(tmp_path)

    assert result.allowed is False
    assert "newer than supported" in result.reason
