"""B2: rollback eligibility requires a stopped candidate and proven absence of OCPP evidence."""
import json
import sqlite3

import pytest

from ocpp_csms.cutover_evidence import has_committed_ocpp_evidence
from ocpp_csms.install_cutover import main
from ocpp_csms.schema import create_current_schema


def test_empty_initialized_candidate_is_rollback_eligible(tmp_path, capsys):
    create_current_schema(tmp_path)
    assert not has_committed_ocpp_evidence(tmp_path)
    assert main(["storage-evidence", "--data-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"committed_ocpp_evidence": False}


@pytest.mark.parametrize("action", ["BootNotification", "Heartbeat", "StartTransaction", "MeterValues"])
def test_committed_ocpp_frame_prohibits_rollback(tmp_path, action):
    create_current_schema(tmp_path)
    with sqlite3.connect(tmp_path / "ocpp-csms.sqlite3") as db:
        db.execute("INSERT INTO events (received_at, charger_id, action, direction, payload_json) "
                   "VALUES ('now', 'cp', ?, 'in', '{}')", (action,))
    assert has_committed_ocpp_evidence(tmp_path)


def test_uncommitted_frame_is_not_evidence(tmp_path):
    create_current_schema(tmp_path)
    with sqlite3.connect(tmp_path / "ocpp-csms.sqlite3") as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO events (received_at, charger_id, action, direction, payload_json) "
                       "VALUES ('now', 'cp', 'Heartbeat', 'in', '{}')")
        assert not has_committed_ocpp_evidence(tmp_path)
        writer.rollback()


def test_derived_ocpp_state_fails_closed(tmp_path):
    create_current_schema(tmp_path)
    with sqlite3.connect(tmp_path / "ocpp-csms.sqlite3") as db:
        db.execute("INSERT INTO connector_status "
                   "(charger_id, connector_id, status, event_timestamp, received_at) "
                   "VALUES ('cp', 1, 'Available', 'now', 'now')")
    assert has_committed_ocpp_evidence(tmp_path)


def test_missing_or_unreadable_candidate_is_uncertain(tmp_path, capsys):
    assert main(["storage-evidence", "--data-dir", str(tmp_path)]) == 1
    assert "missing" in capsys.readouterr().out
    (tmp_path / "ocpp-csms.sqlite3").write_bytes(b"not sqlite")
    assert main(["storage-evidence", "--data-dir", str(tmp_path)]) == 1
    assert "uncertain" in capsys.readouterr().out
