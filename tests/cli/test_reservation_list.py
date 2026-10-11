"""Read-only reservation listing from stored OCPP evidence."""
import json
import sqlite3
from datetime import datetime, timezone
import pytest
from ocpp_csms.schema import create_current_schema, DATABASE_FILENAME
from ocpp_csms.reservations import list_reservations

@pytest.fixture
def reservation_events(tmp_path):
    """Store realistic OCPP events with a fixed observation clock."""
    create_current_schema(tmp_path)
    db = tmp_path / DATABASE_FILENAME
    def record(cp, action, direction, payload, at="2026-10-10T12:00:00Z"):
        with sqlite3.connect(db) as con:
            con.execute("""INSERT INTO events
              (received_at, charger_id, action, direction, payload_json)
              VALUES (?, ?, ?, ?, ?)""", (at, cp, action, direction, json.dumps(payload)))
    def query(**kwargs):
        return list_reservations(tmp_path, now=datetime(2026, 10, 11, tzinfo=timezone.utc), **kwargs)
    return record, query


def test_reservation_lifecycle_and_filters(reservation_events):
    write, query = reservation_events
    expiry = "2026-10-20T12:00:00Z"
    write("CP1", "ReserveNow", "out", {"reservation_id": 42, "connector_id": 1, "id_tag": "ABC", "expiry_date": expiry})
    rows = query()
    assert len(rows) == 1 and rows[0]["status"] == "Requested"
    write("CP1", "ReserveNow", "in", {"status": "Accepted"})
    rows = query()
    assert rows[0]["status"] == "Accepted"
    assert rows[0]["id"] == 42 and rows[0]["cp"] == "CP1"
    write("CP1", "CancelReservation", "out", {"reservation_id": 42})
    write("CP1", "CancelReservation", "in", {"status": "Accepted"})
    assert query() == []
    assert query(include_all=True)[0]["status"] == "Canceled"

def test_ambiguous_responses_never_claim_accepted(reservation_events):
    write, query = reservation_events
    for rid in (42, 43):
        write("CP1", "ReserveNow", "out", {"reservation_id": rid, "connector_id": 1,
              "id_tag": "ABC", "expiry_date": "2026-10-20T12:00:00Z"})
    write("CP1", "ReserveNow", "in", {"status": "Accepted"})
    rows = query()
    assert {r["status"] for r in rows} == {"Requested"}

def test_expired_and_used_are_historical(reservation_events):
    write, query = reservation_events
    write("CP1", "ReserveNow", "out", {"reservation_id": 42, "connector_id": 0, "id_tag": "ABC",
          "expiry_date": "2026-10-10T14:00:00Z"})
    write("CP1", "ReserveNow", "in", {"status": "Accepted"})
    assert query() == []
    assert query(include_all=True)[0]["status"] == "Expired"
    write("CP1", "StartTransaction", "in", {"reservation_id": 42})
    rows = query(include_all=True)
    assert rows[0]["status"] == "Used"

def test_reservation_list_cli_parses(parse_cli):
    args = parse_cli("reservation", "list", "--cp", "CP1", "--all", "--since", "7d", "--json")
    assert args.reservation_action == "list"
    assert args.charger == "CP1" and args.all and args.json
