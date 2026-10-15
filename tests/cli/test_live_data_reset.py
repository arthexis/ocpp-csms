"""Live per-charge-point reset safety and preservation tests."""
import json
import sqlite3
from pathlib import Path

import pytest

from ocpp_csms.cli.appliance import initialize_storage
from ocpp_csms.cli.data import reset_live_cp


def test_live_reset_preserves_other_charger_and_allocator(tmp_path):
    root = tmp_path / "data"
    initialize_storage(str(root))
    database = root / "ocpp-csms.sqlite3"
    with sqlite3.connect(database) as db:
        for tx, cp in ((101, "SIM001"), (102, "ABB001")):
            db.execute("INSERT INTO transactions (transaction_id,charger_id,state,last_activity_at) VALUES (?,?,'stopped','2026-10-15')", (tx, cp))
            db.execute("INSERT INTO events (received_at,charger_id,action,direction,payload_json,transaction_id) VALUES ('2026-10-15',?,'StopTransaction','in','{}',?)", (cp, tx))
        db.execute("UPDATE transaction_allocator SET last_id=102 WHERE singleton=1")
        original_source = db.execute("SELECT value FROM appliance_metadata WHERE key='source_id'").fetchone()[0]
    folder = root / "transactions" / "2026-10-15"
    folder.mkdir(parents=True, exist_ok=True)
    for tx, cp in ((101, "SIM001"), (102, "ABB001")):
        (folder / f"{tx}.json").write_text(json.dumps({"charge_point_id": cp, "transaction_id": tx}))
    result = reset_live_cp(root, "SIM001")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT transaction_id FROM transactions").fetchall() == [(102,)]
        assert db.execute("SELECT charger_id FROM events").fetchall() == [("ABB001",)]
        assert db.execute("SELECT last_id FROM transaction_allocator").fetchone() == (102,)
        assert db.execute("SELECT value FROM appliance_metadata WHERE key='source_id'").fetchone()[0] == original_source
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert not (folder / "101.json").exists()
    assert (folder / "102.json").exists()
    assert (Path(result["backup"]) / "transactions/2026-10-15/101.json").exists()
    assert (Path(result["backup"]) / database.name).is_file()


def test_live_reset_refuses_open_transaction(tmp_path):
    root = tmp_path / "data"
    initialize_storage(str(root))
    with sqlite3.connect(root / "ocpp-csms.sqlite3") as db:
        db.execute("INSERT INTO transactions (transaction_id,charger_id,state,last_activity_at) VALUES (5,'SIM001','active','2026-10-15')")
    with pytest.raises(ValueError, match="open transaction"):
        reset_live_cp(root, "SIM001")
    with sqlite3.connect(root / "ocpp-csms.sqlite3") as db:
        assert db.execute("SELECT transaction_id FROM transactions").fetchone() == (5,)
