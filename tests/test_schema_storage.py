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
