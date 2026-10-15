import pytest
from ocpp_csms.install_cutover import checkpoint_stopped_storage
from ocpp_csms.schema import create_current_schema
from ocpp_csms.schema_storage import adopt_legacy_storage, activate_schema


def test_adopt_legacy_and_activate(tmp_path):
    create_current_schema(tmp_path)
    (tmp_path / "transactions").mkdir(exist_ok=True)
    (tmp_path / "transactions" / "historical.json").write_text("{}")
    # Model the real cutover: stop the writer and checkpoint before moving files.
    checkpoint_stopped_storage(tmp_path)
    adopted = adopt_legacy_storage(tmp_path)
    assert adopted == tmp_path / "schemas" / "8"
    assert (adopted / "transactions" / "historical.json").read_text() == "{}"
    assert not (tmp_path / "ocpp-csms.sqlite3").exists()
    assert activate_schema(tmp_path, adopted).resolve() == adopted.resolve()
    assert activate_schema(tmp_path, adopted).resolve() == adopted.resolve()


def test_adoption_rejects_collision_without_moving_legacy(tmp_path):
    create_current_schema(tmp_path)
    (tmp_path / "transactions").mkdir(exist_ok=True)
    (tmp_path / "schemas" / "8").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="already exists"):
        adopt_legacy_storage(tmp_path)
    assert (tmp_path / "ocpp-csms.sqlite3").exists()


def test_adoption_rejects_sqlite_sidecars(tmp_path):
    create_current_schema(tmp_path)
    (tmp_path / "transactions").mkdir(exist_ok=True)
    (tmp_path / "ocpp-csms.sqlite3-wal").write_bytes(b"pending")
    with pytest.raises(RuntimeError, match="WAL contains data"):
        adopt_legacy_storage(tmp_path)


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


def test_adoption_preserves_empty_checkpointed_sidecars(tmp_path):
    create_current_schema(tmp_path)
    (tmp_path / "transactions").mkdir(exist_ok=True)
    checkpoint_stopped_storage(tmp_path)
    wal = tmp_path / "ocpp-csms.sqlite3-wal"
    shm = tmp_path / "ocpp-csms.sqlite3-shm"
    wal.write_bytes(b"")
    shm.write_bytes(b"checkpointed index")
    adopted = adopt_legacy_storage(tmp_path)
    assert (adopted / wal.name).read_bytes() == b""
    assert (adopted / shm.name).read_bytes() == b"checkpointed index"
    assert not wal.exists()
    assert not shm.exists()


def test_adoption_rejects_rollback_journal(tmp_path):
    create_current_schema(tmp_path)
    (tmp_path / "transactions").mkdir(exist_ok=True)
    checkpoint_stopped_storage(tmp_path)
    (tmp_path / "ocpp-csms.sqlite3-journal").write_bytes(b"pending")
    with pytest.raises(RuntimeError, match="rollback journal"):
        adopt_legacy_storage(tmp_path)
