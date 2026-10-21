"""Live per-charge-point reset safety and preservation tests."""
import json
import sqlite3
import tarfile
import hashlib
from pathlib import Path

import pytest

from ocpp_csms.cli.appliance import initialize_storage
from ocpp_csms.cli.data import reset_live_cp


def test_live_reset_preserves_other_charger_and_allocator(tmp_path):
    root = tmp_path / "data"
    initialize_storage(str(root))
    database = root / "ocpp-csms.sqlite3"
    with sqlite3.connect(database) as db:
        for tx, cp in ((101, "SIM001"), (102, "Vendor001")):
            db.execute("INSERT INTO transactions (transaction_id,charger_id,state,last_activity_at) VALUES (?,?,'stopped','2026-10-15')", (tx, cp))
            db.execute("INSERT INTO events (received_at,charger_id,action,direction,payload_json,transaction_id) VALUES ('2026-10-15',?,'StopTransaction','in','{}',?)", (cp, tx))
        db.execute("UPDATE transaction_allocator SET last_id=102 WHERE singleton=1")
        original_source = db.execute("SELECT value FROM appliance_metadata WHERE key='source_id'").fetchone()[0]
    folder = root / "transactions" / "2026-10-15"
    folder.mkdir(parents=True, exist_ok=True)
    for tx, cp in ((101, "SIM001"), (102, "Vendor001")):
        (folder / f"{tx}.json").write_text(json.dumps({"charge_point_id": cp, "transaction_id": tx}))
    result = reset_live_cp(root, "SIM001")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT transaction_id FROM transactions").fetchall() == [(102,)]
        assert db.execute("SELECT charger_id FROM events").fetchall() == [("Vendor001",)]
        assert db.execute("SELECT last_id FROM transaction_allocator").fetchone() == (102,)
        assert db.execute("SELECT value FROM appliance_metadata WHERE key='source_id'").fetchone()[0] == original_source
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert not (folder / "101.json").exists()
    assert (folder / "102.json").exists()
    assert result["deleted_json"] == 1
    assert Path(result["archive"]).is_file()
    with tarfile.open(result["archive"], "r:gz") as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
        assert manifest["scope"] == "cp"
        assert manifest["cp"] == "SIM001"
        for name, expected in manifest["files"].items():
            assert hashlib.sha256(archive.extractfile("data/" + name).read()).hexdigest() == expected
        assert "ocpp-csms.sqlite3" in manifest["files"]
    assert reset_live_cp(root, "SIM001")["deleted_json"] == 0
    assert Path(result["archive"]).is_file()


def test_live_reset_refuses_open_transaction(tmp_path):
    root = tmp_path / "data"
    initialize_storage(str(root))
    with sqlite3.connect(root / "ocpp-csms.sqlite3") as db:
        db.execute("INSERT INTO transactions (transaction_id,charger_id,state,last_activity_at) VALUES (5,'SIM001','active','2026-10-15')")
    with pytest.raises(ValueError, match="open transaction"):
        reset_live_cp(root, "SIM001")
    with sqlite3.connect(root / "ocpp-csms.sqlite3") as db:
        assert db.execute("SELECT transaction_id FROM transactions").fetchone() == (5,)


def test_live_reset_only_deletes_selected_charger_across_all_tables(tmp_path):
    root = tmp_path / "data"
    initialize_storage(str(root))
    database = root / "ocpp-csms.sqlite3"
    with sqlite3.connect(database) as db:
        for tx, cp in ((201, "SIM001"), (202, "Vendor001"), (203, "Vendor002")):
            db.execute("INSERT INTO transactions (transaction_id,charger_id,state,last_activity_at) VALUES (?,?,'stopped','2026-10-15')", (tx, cp))
            db.execute("INSERT INTO events (received_at,charger_id,action,direction,payload_json,transaction_id) VALUES ('2026-10-15',?,'BootNotification','in','{}',?)", (cp,tx))
            db.execute("INSERT INTO runtime_events (occurred_at,event,charger_id) VALUES ('2026-10-15','charger_disconnected',?)", (cp,))
            db.execute("INSERT INTO connector_status (charger_id,connector_id,status,event_timestamp,received_at) VALUES (?,1,'Available','2026-10-15','2026-10-15')", (cp,))
            db.execute("INSERT INTO rfid_lists (charger_id,list_version,sent_at,list_hash) VALUES (?,1,'2026-10-15','test')", (cp,))
        db.execute("UPDATE transaction_allocator SET last_id=203 WHERE singleton=1")
    folder = root / "transactions" / "2026-10-15"
    folder.mkdir(parents=True, exist_ok=True)
    for tx, cp in ((201, "SIM001"), (202, "Vendor001"), (203, "Vendor002")):
        (folder / f"{tx}.json").write_text(json.dumps({"charge_point_id": cp, "transaction_id": tx}))
    reset_live_cp(root, "SIM001")
    with sqlite3.connect(database) as db:
        for table in ("transactions", "events", "runtime_events", "connector_status", "rfid_lists"):
            assert [row[0] for row in db.execute(f"SELECT charger_id FROM {table} ORDER BY charger_id")] == ["Vendor001", "Vendor002"]
        assert db.execute("SELECT last_id FROM transaction_allocator").fetchone() == (203,)
    assert not (folder / "201.json").exists()
    assert (folder / "202.json").exists()
    assert (folder / "203.json").exists()
    reset_live_cp(root, "SIM001")
    assert (folder / "202.json").exists() and (folder / "203.json").exists()
