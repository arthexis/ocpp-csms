"""Compiled-schema storage and deployment safety tests."""
import sqlite3

import pytest

from ocpp_csms.schema import CURRENT_SCHEMA_VERSION, create_current_schema, source_id
from ocpp_csms.schema_storage import plan_storage, prepare_storage, schema_directory


def test_first_install_prepares_compiled_schema(tmp_path):
    plan = plan_storage(tmp_path)
    assert plan.action == "create"
    assert plan.target == schema_directory(tmp_path)
    assert prepare_storage(plan).version == CURRENT_SCHEMA_VERSION
    assert source_id(plan.target)


def test_same_schema_reuses_identity_allocator_and_path(tmp_path):
    target = schema_directory(tmp_path)
    create_current_schema(target)
    with sqlite3.connect(target / "ocpp-csms.sqlite3") as db:
        db.execute("UPDATE transaction_allocator SET last_id = 42")
    identity = source_id(target)
    plan = plan_storage(tmp_path)
    assert plan.action == "reuse"
    assert plan.target == target
    assert prepare_storage(plan).path == target / "ocpp-csms.sqlite3"
    assert source_id(target) == identity
    with sqlite3.connect(target / "ocpp-csms.sqlite3") as db:
        assert db.execute("SELECT last_id FROM transaction_allocator").fetchone() == (42,)


def test_downgrade_is_rejected_from_existing_schema_directory(tmp_path):
    newer = schema_directory(tmp_path, CURRENT_SCHEMA_VERSION + 1)
    newer.mkdir(parents=True)
    with sqlite3.connect(newer / "ocpp-csms.sqlite3") as db:
        db.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="downgrade"):
        plan_storage(tmp_path)


def test_invalid_prepared_target_is_rejected_without_overwriting(tmp_path):
    target = schema_directory(tmp_path)
    target.mkdir(parents=True)
    (target / "evidence.txt").write_text("preserve")
    with pytest.raises(RuntimeError, match="interrupted or invalid schema preparation"):
        plan_storage(tmp_path)
    assert (target / "evidence.txt").read_text() == "preserve"


def test_installer_storage_commands_reuse_prepared_schema(tmp_path, capsys):
    import json
    from ocpp_csms.install_cutover import main
    root = str(tmp_path)
    assert main(["storage-plan", "--storage-root", root]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["action"] == "create"
    assert main(["storage-prepare", "--storage-root", root]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["target"] == planned["target"]
    assert main(["storage-plan", "--storage-root", root]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "reuse"


def test_unversioned_database_is_not_moved_or_overwritten(tmp_path):
    create_current_schema(tmp_path)
    with pytest.raises(RuntimeError, match="unversioned database"):
        plan_storage(tmp_path)
    assert source_id(tmp_path)


def test_old_pointer_requires_manual_resolution(tmp_path):
    (tmp_path / "current").symlink_to(tmp_path / "missing")
    with pytest.raises(RuntimeError, match="obsolete storage pointer"):
        plan_storage(tmp_path)


def test_same_schema_reuse_preserves_real_transaction_and_archive(tmp_path):
    """A later release using the same compiled schema must not replace evidence."""
    target = schema_directory(tmp_path)
    create_current_schema(target)
    archives = target / "transactions"
    archives.mkdir()
    archived = archives / "transaction-101.json"
    archived.write_text('{"transaction_id":101,"meter_start":1200}')
    with sqlite3.connect(target / "ocpp-csms.sqlite3") as db:
        db.execute(
            """INSERT INTO transactions
               (transaction_id, charger_id, connector_id, id_tag, meter_start,
                started_at, start_received_at, state, last_activity_at)
               VALUES (101, 'charger-ci', 1, 'RFID-CI', 1200,
                       '2026-10-15T00:00:00Z', '2026-10-15T00:00:01Z',
                       'active', '2026-10-15T00:00:01Z')"""
        )
    original_source = source_id(target)
    original_stat = (target / "ocpp-csms.sqlite3").stat()
    for _release in ("commit-A", "commit-B"):
        plan = plan_storage(tmp_path)
        assert plan.action == "reuse"
        assert prepare_storage(plan).path == target / "ocpp-csms.sqlite3"
    with sqlite3.connect(target / "ocpp-csms.sqlite3") as db:
        assert db.execute(
            "SELECT charger_id, meter_start FROM transactions WHERE transaction_id=101"
        ).fetchone() == ("charger-ci", 1200)
    assert source_id(target) == original_source
    assert (target / "ocpp-csms.sqlite3").stat().st_ino == original_stat.st_ino
    assert archived.read_text() == '{"transaction_id":101,"meter_start":1200}'


def test_new_schema_creates_empty_database_without_copying_old_evidence(tmp_path, monkeypatch):
    """Simulate the *storage planner* of a future schema, not a deployed upgrade."""
    import ocpp_csms.schema_storage as storage

    old = schema_directory(tmp_path)
    create_current_schema(old)
    old_archive = old / "transactions"
    old_archive.mkdir()
    (old_archive / "old.json").write_text('{"transaction_id":77}')
    with sqlite3.connect(old / "ocpp-csms.sqlite3") as db:
        db.execute("UPDATE transaction_allocator SET last_id=77")
    old_source = source_id(old)

    # A future release compiles the next schema; model its planner decision.
    original_schema_directory = storage.schema_directory
    monkeypatch.setattr(storage, "CURRENT_SCHEMA_VERSION", CURRENT_SCHEMA_VERSION + 1)
    monkeypatch.setattr(storage, "schema_directory", lambda root: original_schema_directory(root, CURRENT_SCHEMA_VERSION + 1))
    plan = storage.plan_storage(tmp_path)
    assert plan.action == "create"
    assert plan.active_schema == CURRENT_SCHEMA_VERSION
    assert plan.target == schema_directory(tmp_path, CURRENT_SCHEMA_VERSION + 1)
    assert not plan.target.exists()
    # A plan must never migrate or modify old transaction evidence.
    assert (old_archive / "old.json").read_text() == '{"transaction_id":77}'
    assert source_id(old) == old_source
    with sqlite3.connect(old / "ocpp-csms.sqlite3") as db:
        assert db.execute("SELECT last_id FROM transaction_allocator").fetchone() == (77,)


def test_storage_preparation_failure_preserves_existing_database(tmp_path, monkeypatch):
    """Failure before cutover must not change the incumbent schema storage."""
    import ocpp_csms.schema_storage as storage

    old = schema_directory(tmp_path)
    create_current_schema(old)
    identity = source_id(old)
    original_schema_directory = storage.schema_directory
    monkeypatch.setattr(storage, "CURRENT_SCHEMA_VERSION", CURRENT_SCHEMA_VERSION + 1)
    monkeypatch.setattr(storage, "schema_directory", lambda root: original_schema_directory(root, CURRENT_SCHEMA_VERSION + 1))
    plan = storage.plan_storage(tmp_path)

    def fail_before_create(_plan):
        raise RuntimeError("injected preparation failure")

    monkeypatch.setattr(storage, "create_current_schema", fail_before_create)
    with pytest.raises(RuntimeError, match="injected preparation failure"):
        storage.prepare_storage(plan)
    assert source_id(old) == identity
    assert not plan.target.joinpath("ocpp-csms.sqlite3").exists()
