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


def test_installer_storage_commands_are_separate_from_legacy_ansible(tmp_path, capsys):
    import json
    from ocpp_csms.install_cutover import main
    root = str(tmp_path)
    assert main(["storage-plan", "--storage-root", root]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["action"] == "create"
    assert not (tmp_path / "schemas").exists()
    assert main(["storage-prepare", "--storage-root", root]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["target"] == planned["target"]
    assert main(["storage-prepare", "--storage-root", root]) == 1
    assert "versioned storage exists without an active current pointer" in capsys.readouterr().out


def test_installer_storage_plan_rejects_downgrade(tmp_path, capsys):
    from ocpp_csms.install_cutover import main
    active = tmp_path / "active"
    active.mkdir()
    with sqlite3.connect(active / "ocpp-csms.sqlite3") as db:
        db.execute("PRAGMA user_version = 99")
    assert main(["storage-plan", "--storage-root", str(tmp_path),
                 "--active-data-dir", str(active)]) == 1
    assert "downgrade" in capsys.readouterr().out


def test_discovery_finds_legacy_root_without_mutating_it(tmp_path):
    from ocpp_csms.schema_storage import discover_active_storage
    create_current_schema(tmp_path)
    before = source_id(tmp_path)
    assert discover_active_storage(tmp_path) == tmp_path
    assert plan_storage(tmp_path, discover_active_storage(tmp_path)).action == "reuse"
    assert source_id(tmp_path) == before
    assert not (tmp_path / "schemas").exists()


def test_discovery_finds_versioned_current_pointer(tmp_path):
    from ocpp_csms.schema_storage import discover_active_storage
    active = tmp_path / "schemas" / str(CURRENT_SCHEMA_VERSION)
    create_current_schema(active)
    (tmp_path / "current").symlink_to(active, target_is_directory=True)
    assert discover_active_storage(tmp_path) == active
    assert plan_storage(tmp_path, discover_active_storage(tmp_path)).action == "reuse"


def test_discovery_rejects_broken_or_ambiguous_pointer(tmp_path):
    from ocpp_csms.schema_storage import discover_active_storage
    (tmp_path / "current").symlink_to(tmp_path / "missing")
    with pytest.raises((OSError, RuntimeError)):
        discover_active_storage(tmp_path)
    (tmp_path / "current").unlink()
    (tmp_path / "schemas").mkdir()
    with pytest.raises(RuntimeError, match="without an active"):
        discover_active_storage(tmp_path)


def test_storage_prepare_does_not_activate_or_modify_legacy(tmp_path):
    from ocpp_csms.schema_storage import discover_active_storage
    legacy = tmp_path / "ocpp-csms.sqlite3"
    with sqlite3.connect(legacy) as db:
        db.execute("PRAGMA user_version = 7")
    before = legacy.read_bytes()
    plan = plan_storage(tmp_path, discover_active_storage(tmp_path))
    prepare_storage(plan)
    assert legacy.read_bytes() == before
    assert not (tmp_path / "current").exists()
    assert plan.target.is_dir()



def test_legacy_and_unactivated_candidate_are_ambiguous(tmp_path):
    from ocpp_csms.schema_storage import discover_active_storage
    create_current_schema(tmp_path)
    (tmp_path / "schemas" / str(CURRENT_SCHEMA_VERSION)).mkdir(parents=True)
    with pytest.raises(RuntimeError, match="ambiguous"):
        discover_active_storage(tmp_path)
    assert (tmp_path / "ocpp-csms.sqlite3").exists()


def test_versioned_pointer_rejects_wrong_schema_or_nested_directory(tmp_path):
    from ocpp_csms.schema_storage import discover_active_storage
    target = tmp_path / "schemas" / "7"
    create_current_schema(target)
    pointer = tmp_path / "current"
    pointer.symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match="mismatched"):
        discover_active_storage(tmp_path)
    pointer.unlink()
    nested = tmp_path / "schemas" / str(CURRENT_SCHEMA_VERSION) / "nested"
    create_current_schema(nested)
    pointer.symlink_to(nested, target_is_directory=True)
    with pytest.raises(RuntimeError, match="invalid active schema pointer"):
        discover_active_storage(tmp_path)


def test_versioned_pointer_rejects_missing_database(tmp_path):
    from ocpp_csms.schema_storage import discover_active_storage
    target = tmp_path / "schemas" / str(CURRENT_SCHEMA_VERSION)
    target.mkdir(parents=True)
    (tmp_path / "current").symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match="missing database"):
        discover_active_storage(tmp_path)
