"""Focused Chunk A tests: schema versions select immutable historical stores."""
import sqlite3

import pytest

from ocpp_csms.schema import CURRENT_SCHEMA_VERSION, create_current_schema, source_id
from ocpp_csms.schema_storage import plan_storage, prepare_storage, schema_directory


def test_first_install_prepares_current_schema(tmp_path):
    plan = plan_storage(tmp_path, None)
    assert plan.action == "create"
    assert plan.target == schema_directory(tmp_path, CURRENT_SCHEMA_VERSION)
    info = prepare_storage(plan)
    assert info.version == CURRENT_SCHEMA_VERSION
    assert source_id(plan.target)


def test_same_schema_reuses_identity_allocator_and_path(tmp_path):
    active = tmp_path / "existing"
    create_current_schema(active)
    with sqlite3.connect(active / "ocpp-csms.sqlite3") as db:
        db.execute("UPDATE transaction_allocator SET last_id = 42")
    identity = source_id(active)
    plan = plan_storage(tmp_path, active)
    assert plan.action == "reuse"
    assert plan.target == active
    assert prepare_storage(plan).path == active / "ocpp-csms.sqlite3"
    assert source_id(active) == identity
    with sqlite3.connect(active / "ocpp-csms.sqlite3") as db:
        assert db.execute("SELECT last_id FROM transaction_allocator").fetchone() == (42,)


def test_upgrade_prepares_empty_new_schema_without_touching_old(tmp_path):
    old = tmp_path / "schemas" / "7"
    old.mkdir(parents=True)
    with sqlite3.connect(old / "ocpp-csms.sqlite3") as db:
        db.execute("PRAGMA user_version = 7")
        db.execute("CREATE TABLE evidence (value TEXT)")
        db.execute("INSERT INTO evidence VALUES ('historical')")
    before = (old / "ocpp-csms.sqlite3").read_bytes()
    plan = plan_storage(tmp_path, old)
    assert plan.action == "create"
    assert plan.active_schema == 7
    prepare_storage(plan)
    assert (old / "ocpp-csms.sqlite3").read_bytes() == before
    assert (old / "ocpp-csms.sqlite3").exists()
    with sqlite3.connect(plan.target / "ocpp-csms.sqlite3") as db:
        assert db.execute("SELECT last_id FROM transaction_allocator").fetchone() == (0,)


def test_downgrade_is_rejected(tmp_path):
    active = tmp_path / "active"
    create_current_schema(active)
    with pytest.raises(RuntimeError, match="downgrade"):
        plan_storage(tmp_path, active, candidate_version=CURRENT_SCHEMA_VERSION - 1)


def test_existing_target_directory_is_never_reused_or_overwritten(tmp_path):
    target = schema_directory(tmp_path, CURRENT_SCHEMA_VERSION)
    target.mkdir(parents=True)
    (target / "evidence.txt").write_text("preserve")
    with pytest.raises(RuntimeError, match="already exists"):
        plan_storage(tmp_path, None)
    assert (target / "evidence.txt").read_text() == "preserve"


def test_missing_or_unversioned_active_database_is_rejected(tmp_path):
    with pytest.raises(RuntimeError, match="missing"):
        plan_storage(tmp_path, tmp_path / "missing")
    active = tmp_path / "unversioned"
    active.mkdir()
    with sqlite3.connect(active / "ocpp-csms.sqlite3") as db:
        db.execute("CREATE TABLE unknown (id INTEGER)")
    with pytest.raises(RuntimeError, match="invalid schema"):
        plan_storage(tmp_path, active)
