"""B3 checkpoint gate tests; activation wiring follows separately."""
import sqlite3

from ocpp_csms.install_cutover import main
from ocpp_csms.schema import create_current_schema


def test_stopped_candidate_checkpoint_is_successful(tmp_path, capsys):
    create_current_schema(tmp_path)
    assert main(["storage-checkpoint", "--data-dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "ok"


def test_checkpoint_does_not_create_missing_database(tmp_path, capsys):
    assert main(["storage-checkpoint", "--data-dir", str(tmp_path)]) == 1
    assert "missing" in capsys.readouterr().out
    assert not (tmp_path / "ocpp-csms.sqlite3").exists()


def test_checkpoint_fails_with_live_writer(tmp_path, capsys):
    create_current_schema(tmp_path)
    with sqlite3.connect(tmp_path / "ocpp-csms.sqlite3") as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO events (received_at, charger_id, action, direction, payload_json) "
                       "VALUES ('now', 'cp', 'Heartbeat', 'in', '{}')")
        assert main(["storage-checkpoint", "--data-dir", str(tmp_path)]) == 1
        assert "incomplete" in capsys.readouterr().out
        writer.rollback()
