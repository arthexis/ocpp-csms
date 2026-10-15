import sqlite3

import pytest
from ocpp_csms.install_cutover import main as cutover_main
from ocpp_csms.schema import CURRENT_SCHEMA_VERSION, create_current_schema
from ocpp_csms.schema_storage import activate_schema, plan_storage, prepare_storage


def test_fresh_schema_activation_preserves_legacy_storage(tmp_path):
    legacy = tmp_path / "ocpp-csms.sqlite3"
    with sqlite3.connect(legacy) as db:
        db.execute("PRAGMA user_version = 7")
    before = legacy.read_bytes()
    plan = plan_storage(tmp_path, tmp_path)
    prepare_storage(plan)
    assert activate_schema(tmp_path, plan.target).resolve() == plan.target.resolve()
    assert activate_schema(tmp_path, plan.target).resolve() == plan.target.resolve()
    assert legacy.read_bytes() == before


def test_activation_rejects_external_target(tmp_path):
    external = tmp_path.parent / (tmp_path.name + "-external")
    create_current_schema(external)
    with pytest.raises((RuntimeError, FileNotFoundError)):
        activate_schema(tmp_path, external)


def test_activation_does_not_replace_regular_current(tmp_path):
    target = tmp_path / "schemas" / str(CURRENT_SCHEMA_VERSION)
    create_current_schema(target)
    (tmp_path / "current").write_text("occupied")
    with pytest.raises(RuntimeError, match="not a symlink"):
        activate_schema(tmp_path, target)
    assert (tmp_path / "current").read_text() == "occupied"


def test_storage_activate_cli_rejects_external_target(tmp_path, capsys):
    external = tmp_path.parent / (tmp_path.name + "-external-cli")
    create_current_schema(external)
    assert cutover_main(["storage-activate", "--storage-root", str(tmp_path), "--target", str(external)]) == 1
    assert not (tmp_path / "current").exists()
