"""Persistent WAL availability for read-only consumers after owner closes."""
import sqlite3
from contextlib import closing

from ocpp_csms.persistent_wal import persistent_wal


def test_wal_sidecars_survive_last_writer_close(tmp_path):
    path = tmp_path / "ocpp-csms.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE evidence (id INTEGER)")
        db.execute("INSERT INTO evidence VALUES (1)")
    with persistent_wal(tmp_path):
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO evidence VALUES (2)")
        assert (tmp_path / "ocpp-csms.sqlite3-wal").exists()
        assert (tmp_path / "ocpp-csms.sqlite3-shm").exists()
    assert (tmp_path / "ocpp-csms.sqlite3-wal").exists()
    assert (tmp_path / "ocpp-csms.sqlite3-shm").exists()
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 2
