"""Stage 1A negative contracts: characterize legacy seams without modifying production.

All filesystem writes are confined to pytest tmp_path. No services, sockets,
installed state, field nodes, release scripts or network calls are exercised.
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from ocpp_csms import schema, tls_config
from ocpp_csms.cli import tls as cli_tls
from ocpp_csms.cli.data import _remove_cp_from_copy
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.tls_listener import TLSListener
from ocpp_csms.transactions.archive import TransactionArchive
from ocpp_csms.transactions.query import TransactionQuery
from ocpp_discover.first_contact import _session_proven


def _status_args(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(tls_command="status",
                           config_path=str(tmp_path / "missing-tls.json"),
                           data_dir=str(tmp_path))


def test_tls_missing_static_and_live_json_remain_distinct(tmp_path):
    missing = tmp_path / "missing-tls.json"
    assert tls_config.status(missing) == {
        "configured": False, "enabled": False, "listener": "not_implemented",
    }
    assert TLSListener(missing).status() == {
        "configured": False, "enabled": False, "listener": "stopped",
        "active_port": None, "draining_ports": [], "error": None,
    }
    assert not missing.exists()


def test_tls_invalid_config_fails_closed_without_guessing_live_state(tmp_path):
    path = tmp_path / "tls.json"
    path.write_text("{invalid", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_tls_configuration"):
        tls_config.status(path)
    assert TLSListener(path).status() == {
        "configured": False, "error": "invalid_tls_configuration",
        "listener": "stopped", "active_port": None,
        "draining_ports": [], "error": None,
    }


@pytest.mark.parametrize("mode", ["denied", "connection_error", "live"])
def test_tls_cli_status_control_fallback_is_exact_json(tmp_path, monkeypatch, capsys, mode):
    async def pretend_control(*_args, **_kwargs):
        if mode == "connection_error":
            raise OSError("local control unavailable")
        if mode == "denied":
            return {"ok": False, "error": "local_control_unavailable"}
        return {"ok": True, "response": {
            "configured": True, "enabled": True, "listener": "listening",
            "active_port": 9443, "draining_ports": [], "error": None,
        }}
    monkeypatch.setattr(cli_tls, "send_control", pretend_control)
    assert cli_tls.run_tls(_status_args(tmp_path)) == 0
    rendered = json.loads(capsys.readouterr().out)
    if mode == "live":
        assert rendered == ({"configured": True, "enabled": True,
                             "listener": "listening", "active_port": 9443,
                             "draining_ports": [], "error": None})
    else:
        assert rendered == tls_config.status(tmp_path / "missing-tls.json")


def _objects(db: sqlite3.Connection):
    return db.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()


def test_current_schema_v8_matches_composition_excluding_legacy_v5_v6(tmp_path):
    # This is a structural guard, not a migration or a replacement schema.
    parts = (
        schema._SCHEMA_1_SQL, schema._SCHEMA_2_ADDITIONS_SQL,
        schema._SCHEMA_3_ADDITIONS_SQL, schema._SCHEMA_4_ADDITIONS_SQL,
        schema._SCHEMA_7_ADDITIONS_SQL, schema._SCHEMA_8_ADDITIONS_SQL,
    )
    expected = "".join(parts).replace(
        "CREATE TABLE IF NOT EXISTS connector_status (",
        "CREATE TABLE IF NOT EXISTS connector_status (\n    info TEXT,", 1,
    )
    assert expected == schema.CURRENT_SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS transaction_recoveries" in schema._SCHEMA_5_ADDITIONS_SQL
    assert "ALTER TABLE connector_status ADD COLUMN info" in schema._SCHEMA_6_ADDITIONS_SQL
    assert "DROP TABLE IF EXISTS transaction_recoveries;" in expected
    with closing(sqlite3.connect(":memory:")) as independent:
        independent.executescript(expected)
        info = schema.create_current_schema(tmp_path)
        with closing(sqlite3.connect(info.path)) as installed:
            assert schema.CURRENT_SCHEMA_VERSION == 8
            assert installed.execute("PRAGMA user_version").fetchone() == (8,)
            assert _objects(installed) == _objects(independent)
            columns = [row[1] for row in installed.execute(
                "PRAGMA table_info(connector_status)"
            )]
            assert "info" in columns
            names = {row[1] for row in _objects(installed)}
            assert "transaction_recoveries" not in names
            assert {"transaction_summary", "transaction_boundaries",
                    "start_identities", "transaction_allocator"} <= names


def test_discover_legacy_schema_without_session_tables_denies_proof(tmp_path):
    schema.create_current_schema(tmp_path)
    assert not _session_proven(tmp_path, "cp-1", "192.0.2.10", {41000}, 0)
    assert not _session_proven(tmp_path, "cp-1", "192.0.2.10", set(), 0)


def test_discover_proof_requires_exact_unique_session_and_new_inbound_event(tmp_path):
    store = EventStore(tmp_path)
    assert not _session_proven(tmp_path, "cp-1", "192.0.2.10", {41000}, 0)
    store.record_session("session-1", "cp-1", "192.0.2.10", 41000)
    assert not _session_proven(tmp_path, "cp-1", "192.0.2.10", {41000}, 0)
    store.record_ocpp("cp-1", "Heartbeat", {}, direction="in",
                      session_id="session-1")
    assert _session_proven(tmp_path, "cp-1", "192.0.2.10", {41000}, 0)
    assert not _session_proven(tmp_path, "cp-1", "192.0.2.11", {41000}, 0)
    with closing(sqlite3.connect(store.path)) as db:
        watermark = db.execute("SELECT MAX(id) FROM events").fetchone()[0]
    assert not _session_proven(tmp_path, "cp-1", "192.0.2.10",
                               {41000}, watermark)
    store.record_session("session-2", "cp-1", "192.0.2.10", 41000)
    assert not _session_proven(tmp_path, "cp-1", "192.0.2.10", {41000}, 0)


@pytest.mark.asyncio
async def test_diagnostic_archive_unknown_and_conflicting_evidence_never_fabricates(tmp_path):
    archive = TransactionArchive(tmp_path)
    unknown = {"transaction_id": 57, "meter_value": []}
    assert await archive.meter_values("cp-1", unknown) is None
    assert await archive.stop("cp-1", {"transaction_id": 57}) is None
    assert not list((tmp_path / "transactions").rglob("*.json"))
    start = {"connector_id": 1, "timestamp": "2026-10-01T00:00:00Z",
             "meter_start": 100, "id_tag": "card"}
    await archive.diagnostic_start("cp-1", start, 57)
    path, = (tmp_path / "transactions").rglob("*.json")
    original = path.read_bytes()
    assert await archive.meter_values("cp-2", {"transaction_id": 57,
                                             "meter_value": []}) is None
    assert await archive.stop("cp-1", {"transaction_id": 57,
                                      "connector_id": 2}) is None
    assert await archive.stop("cp-1", {"transaction_id": 57,
                                      "timestamp": "2026-09-30T23:59:59Z"}) is None
    assert path.read_bytes() == original
    assert archive._conflict_reason(
        json.loads(original), "cp-2", {"transaction_id": 57}
    ) == "charge_point_mismatch"


def test_event_store_stop_adapter_uses_sqlite_without_updating_optional_json(tmp_path):
    store = EventStore(tmp_path)
    start = {"connector_id": 1, "timestamp": "2026-10-01T00:00:00Z",
             "meter_start": 100, "id_tag": "card"}
    transaction_id = store.commit_start("cp-1", start)
    stop = {"transaction_id": transaction_id, "timestamp": "2026-10-01T00:01:00Z",
            "meter_stop": 150}
    store.record_transaction_stop("cp-1", stop)
    store.record_transaction_stop("cp-1", stop)  # idempotent duplicate
    view = TransactionQuery(tmp_path).get(transaction_id)
    assert view is not None and view.status == "stopped"
    assert view.record["energy_wh"] == 50
    assert not list((tmp_path / "transactions").rglob("*.json"))


@pytest.mark.parametrize(
    ("payload", "error"),
    [("{invalid", "unreadable transaction evidence"),
     ('{"transaction_id": 57}', "ambiguous transaction evidence")],
)
def test_reset_copy_refuses_bad_json_before_touching_sqlite(tmp_path, payload, error):
    schema.create_current_schema(tmp_path)
    path = tmp_path / "transactions" / "2026-10-01" / "cp-1-57.json"
    path.parent.mkdir(parents=True)
    path.write_text(payload, encoding="utf-8")
    with closing(sqlite3.connect(schema.database_path(tmp_path))) as db:
        original = db.execute(
            "SELECT value FROM appliance_metadata WHERE key = 'source_id'"
        ).fetchone()
    with pytest.raises(ValueError, match=error):
        _remove_cp_from_copy(tmp_path, "cp-1")
    assert path.read_text(encoding="utf-8") == payload
    with closing(sqlite3.connect(schema.database_path(tmp_path))) as db:
        assert db.execute(
            "SELECT value FROM appliance_metadata WHERE key = 'source_id'"
        ).fetchone() == original


def test_legacy_facade_dynamic_imports_and_monkeypatch_seam(monkeypatch):
    parser = importlib.import_module("ocpp_csms.cli.parser")
    contracts = importlib.import_module("ocpp_csms.contracts")
    control = importlib.import_module("ocpp_csms.control")
    transport = importlib.import_module("ocpp_csms.control.transport")
    actual_parser = importlib.import_module("ocpp_csms.cli.appliance")
    assert parser.add_appliance_commands is actual_parser.add_appliance_commands
    assert control.send_control is transport.send_control
    assert control.os is os
    assert "os" not in control.__all__
    assert contracts.ALL_CONTRACTS == {
        **contracts.SUPPORTED_CONTRACTS, **contracts.DIAGNOSTIC_CONTRACTS,
    }
    status_schema = importlib.import_module("ocpp_csms.status_contract").STATUS_SCHEMA
    assert contracts.SUPPORTED_CONTRACTS["status"] is status_schema
    sentinel = object()
    monkeypatch.setattr(control, "os", sentinel)
    assert control.os is sentinel
