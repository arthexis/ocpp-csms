import sqlite3
import pytest
from ocpp_csms.install_cutover import main as cutover_main
from ocpp_csms.schema import create_current_schema
from ocpp_csms.schema_storage import adopt_legacy_storage, activate_schema


def test_adopt_legacy_and_activate(tmp_path):
    create_current_schema(tmp_path)
    (tmp_path / "transactions").mkdir(exist_ok=True)
    (tmp_path / "transactions" / "historical.json").write_text("{}")
    with sqlite3.connect(tmp_path / "ocpp-csms.sqlite3") as db:
        db.execute("INSERT INTO runtime_events (occurred_at, event) VALUES ('now', 'test')")
    adopted = adopt_legacy_storage(tmp_path)
    assert adopted == tmp_path / "schemas" / "8"
    assert (adopted / "transactions" / "historical.json").read_text() == "{}"
    assert not (tmp_path / "ocpp-csms.sqlite3").exists()
    with sqlite3.connect(adopted / "ocpp-csms.sqlite3") as db:
        assert db.execute("SELECT event FROM runtime_events").fetchone() == ("test",)
    assert activate_schema(tmp_path, adopted).resolve() == adopted.resolve()
    assert activate_schema(tmp_path, adopted).resolve() == adopted.resolve()


def test_adoption_rejects_collision_without_moving_legacy(tmp_path):
    create_current_schema(tmp_path)
    (tmp_path / "transactions").mkdir(exist_ok=True)
    (tmp_path / "schemas" / "8").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="already exists"):
        adopt_legacy_storage(tmp_path)
    assert (tmp_path / "ocpp-csms.sqlite3").exists()


def test_adoption_rejects_live_writer(tmp_path):
    create_current_schema(tmp_path)
    (tmp_path / "transactions").mkdir(exist_ok=True)
    with sqlite3.connect(tmp_path / "ocpp-csms.sqlite3", timeout=0.2) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError, match="checkpoint"):
            adopt_legacy_storage(tmp_path)
        writer.rollback()
    assert (tmp_path / "ocpp-csms.sqlite3").exists()


def test_activation_rejects_external_target(tmp_path):
    external = tmp_path.parent / (tmp_path.name + "-external")
    create_current_schema(external)
    with pytest.raises((RuntimeError, FileNotFoundError)):
        activate_schema(tmp_path, external)


def test_activation_does_not_replace_regular_current(tmp_path):
    target = tmp_path / "schemas" / "8"
    create_current_schema(target)
    (tmp_path / "current").write_text("occupied")
    with pytest.raises(RuntimeError, match="not a symlink"):
        activate_schema(tmp_path, target)
    assert (tmp_path / "current").read_text() == "occupied"


def test_adoption_handles_sqlite_sidecars_without_byte_assumptions(tmp_path):
    create_current_schema(tmp_path)
    (tmp_path / "transactions").mkdir(exist_ok=True)
    adopted = adopt_legacy_storage(tmp_path)
    assert (adopted / "ocpp-csms.sqlite3").exists()
    assert not (tmp_path / "ocpp-csms.sqlite3-wal").exists()
    assert not (tmp_path / "ocpp-csms.sqlite3-shm").exists()


def test_adoption_rejects_unsafe_sidecar(tmp_path):
    create_current_schema(tmp_path)
    (tmp_path / "transactions").mkdir(exist_ok=True)
    (tmp_path / "ocpp-csms.sqlite3-journal").symlink_to("other")
    with pytest.raises(RuntimeError, match="unsafe SQLite sidecar"):
        adopt_legacy_storage(tmp_path)


def test_storage_adopt_and_activate_cli(tmp_path, capsys):
    create_current_schema(tmp_path)
    (tmp_path / "transactions").mkdir(exist_ok=True)
    assert cutover_main(["storage-adopt", "--storage-root", str(tmp_path)]) == 0
    target = tmp_path / "schemas" / "8"
    assert target.is_dir()
    assert cutover_main(["storage-activate", "--storage-root", str(tmp_path), "--target", str(target)]) == 0
    assert (tmp_path / "current").resolve() == target.resolve()


def test_storage_activate_cli_rejects_external_target(tmp_path, capsys):
    external = tmp_path.parent / (tmp_path.name + "-external-cli")
    create_current_schema(external)
    assert cutover_main(["storage-activate", "--storage-root", str(tmp_path), "--target", str(external)]) == 1
    assert not (tmp_path / "current").exists()
