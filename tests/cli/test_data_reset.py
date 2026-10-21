"""Safety contracts for explicitly requested destructive test-data reset."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ocpp_csms.cli.data import reset_test_data



def test_default_dry_run_is_read_only(tmp_path):
    data = tmp_path / "ocpp-csms-data"
    data.mkdir()
    (data / "marker").write_text("test")
    with patch("ocpp_csms.cli.data.process_is_running", return_value=False), \
         patch("ocpp_csms.cli.data.subprocess.run", return_value=SimpleNamespace(returncode=3)):
        result = reset_test_data(data, all_data=True)
    assert result["decision"] == "dry-run"
    assert (data / "marker").read_text() == "test"
    assert not (tmp_path / "ocpp-csms-data-test-backups").exists()


def test_reset_moves_data_and_reinitializes(tmp_path):
    data = tmp_path / "ocpp-csms-data"
    data.mkdir()
    (data / "marker").write_text("secret")
    with patch("ocpp_csms.cli.data.process_is_running", return_value=False), \
         patch("ocpp_csms.cli.data.subprocess.run", return_value=SimpleNamespace(returncode=3)), \
         patch("ocpp_csms.cli.data.initialize_storage") as init:
        result = reset_test_data(data, all_data=True, now=True)
    assert result["decision"] == "reset"
    assert (Path(result["backup"]) / "marker").read_text() == "secret"
    init.assert_called_once_with(str(data))


@pytest.mark.parametrize("reason, running, active", [
    ("running process", True, False), ("active service", False, True),
])
def test_refuses_live_service(tmp_path, reason, running, active):
    data = tmp_path / "data"
    data.mkdir()
    with patch("ocpp_csms.cli.data.process_is_running", return_value=running), \
         patch("ocpp_csms.cli.data.subprocess.run", return_value=SimpleNamespace(returncode=0 if active else 3)):
        with pytest.raises(ValueError):
            reset_test_data(data, all_data=True, now=True)
    assert data.exists()


def test_refuses_without_scope(tmp_path):
    with pytest.raises(ValueError, match="exactly one"):
        reset_test_data(tmp_path, all_data=False, now=True)


def test_refuses_symlinked_data(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    link = tmp_path / "link"
    link.symlink_to(data)
    with pytest.raises(ValueError, match="symlinked"):
        reset_test_data(link, all_data=True, now=True)

def test_cp_scope_keeps_other_charge_point_data(tmp_path):
    from ocpp_csms.cli.appliance import initialize_storage
    import sqlite3
    import json
    data = tmp_path / "ocpp-csms-data"
    initialize_storage(str(data))
    db_path = data / "ocpp-csms.sqlite3"
    with sqlite3.connect(db_path) as db:
        for i, cp in enumerate(("Vendor-A", "Vendor-B"), 1):
            db.execute("INSERT INTO transactions (transaction_id,charger_id,state,last_activity_at) VALUES (?,?,'stopped','2026-01-01')",(i,cp))
            db.execute("INSERT INTO events (received_at,charger_id,action,direction,payload_json,transaction_id) VALUES ('2026-01-01',?,'BootNotification','in','{}',?)",(cp,i))
            db.execute("INSERT INTO runtime_events (occurred_at,event,charger_id) VALUES ('2026-01-01','charger_disconnected',?)",(cp,))
            db.execute("INSERT INTO connector_status (charger_id,connector_id,status,event_timestamp,received_at) VALUES (?,1,'Available','2026-01-01','2026-01-01')",(cp,))
        source_id = db.execute("SELECT value FROM appliance_metadata WHERE key='source_id'").fetchone()[0]
    folder = data / "transactions" / "2026-01-01"
    folder.mkdir(parents=True,exist_ok=True)
    for i, cp in enumerate(("Vendor-A", "Vendor-B"), 1):
        (folder / f"record-{i}.json").write_text(json.dumps({"charge_point_id":cp,"transaction_id":i}))
    with patch("ocpp_csms.cli.data.process_is_running", return_value=False), \
         patch("ocpp_csms.cli.data.subprocess.run", return_value=SimpleNamespace(returncode=3)):
        out = reset_test_data(data, cp="Vendor-A", now=True)
    assert out["scope"] == "cp"
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT charger_id FROM transactions").fetchall() == [("Vendor-B",)]
        assert db.execute("SELECT charger_id FROM events").fetchall() == [("Vendor-B",)]
        assert db.execute("SELECT charger_id FROM runtime_events").fetchall() == [("Vendor-B",)]
        assert db.execute("SELECT charger_id FROM connector_status").fetchall() == [("Vendor-B",)]
        assert db.execute("SELECT value FROM appliance_metadata WHERE key='source_id'").fetchone()[0] == source_id
    assert not (folder / "record-1.json").exists()
    assert (folder / "record-2.json").exists()
    assert (Path(out["backup"]) / "transactions/2026-01-01/record-1.json").exists()


def test_refuses_ambiguous_cp_archive_without_swapping(tmp_path):
    from ocpp_csms.cli.appliance import initialize_storage
    data = tmp_path / "data"
    initialize_storage(str(data))
    folder = data / "transactions" / "2026-01-01"
    folder.mkdir()
    (folder / "unknown.json").write_text("{}")
    with patch("ocpp_csms.cli.data.process_is_running", return_value=False), \
         patch("ocpp_csms.cli.data.subprocess.run", return_value=SimpleNamespace(returncode=3)):
        with pytest.raises(ValueError,match="ambiguous"):
            reset_test_data(data, cp="Vendor-A", now=True)
    assert (folder / "unknown.json").exists()


def test_requires_exclusive_scope(tmp_path):
    with pytest.raises(ValueError, match="exactly one"):
        reset_test_data(tmp_path, all_data=True, cp="Vendor-A")


def test_cp_reset_uses_consistent_wal_snapshot(tmp_path):
    """Committed WAL frames must survive staging even without a checkpoint."""
    import sqlite3
    from ocpp_csms.cli.appliance import initialize_storage

    data = tmp_path / "ocpp-csms-data"
    initialize_storage(str(data))
    database = data / "ocpp-csms.sqlite3"
    writer = sqlite3.connect(database)
    try:
        assert writer.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "INSERT INTO transactions (transaction_id, charger_id, state, last_activity_at) "
            "VALUES (701, 'KEEP', 'stopped', '2026-01-01')"
        )
        writer.commit()
        with patch("ocpp_csms.cli.data.process_is_running", return_value=False), \
             patch("ocpp_csms.cli.data.subprocess.run", return_value=SimpleNamespace(returncode=3)):
            result = reset_test_data(data, cp="REMOVE", now=True)
        with sqlite3.connect(data / "ocpp-csms.sqlite3") as check:
            assert check.execute(
                "SELECT charger_id FROM transactions WHERE transaction_id=701"
            ).fetchone() == ("KEEP",)
            assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert Path(result["backup"]).exists()
    finally:
        writer.close()
