"""Read-only reservation listing from stored OCPP evidence."""
import json
import sqlite3
from datetime import datetime, timezone
from ocpp_csms.schema import create_current_schema, DATABASE_FILENAME
from ocpp_csms.reservations import list_reservations

def write(db, cp, action, direction, payload, at="2026-10-10T12:00:00Z"):
    with sqlite3.connect(db) as con:
        con.execute("""INSERT INTO events
          (received_at, charger_id, action, direction, payload_json)
          VALUES (?, ?, ?, ?, ?)""", (at, cp, action, direction, json.dumps(payload)))

def test_reservation_lifecycle_and_filters(tmp_path):
    create_current_schema(tmp_path)
    db = tmp_path / DATABASE_FILENAME
    expiry = "2026-10-20T12:00:00Z"
    write(db, "CP1", "ReserveNow", "out", {"reservation_id": 42, "connector_id": 1, "id_tag": "ABC", "expiry_date": expiry})
    rows = list_reservations(tmp_path, now=datetime(2026, 10, 11, tzinfo=timezone.utc))
    assert len(rows) == 1 and rows[0]["status"] == "Requested"
    write(db, "CP1", "ReserveNow", "in", {"status": "Accepted"})
    rows = list_reservations(tmp_path, now=datetime(2026, 10, 11, tzinfo=timezone.utc))
    assert rows[0]["status"] == "Accepted"
    assert rows[0]["id"] == 42 and rows[0]["cp"] == "CP1"
    write(db, "CP1", "CancelReservation", "out", {"reservation_id": 42})
    write(db, "CP1", "CancelReservation", "in", {"status": "Accepted"})
    assert list_reservations(tmp_path, now=datetime(2026, 10, 11, tzinfo=timezone.utc)) == []
    assert list_reservations(tmp_path, include_all=True, now=datetime(2026, 10, 11, tzinfo=timezone.utc))[0]["status"] == "Canceled"

def test_ambiguous_responses_never_claim_accepted(tmp_path):
    create_current_schema(tmp_path)
    db = tmp_path / DATABASE_FILENAME
    for rid in (42, 43):
        write(db, "CP1", "ReserveNow", "out", {"reservation_id": rid, "connector_id": 1,
              "id_tag": "ABC", "expiry_date": "2026-10-20T12:00:00Z"})
    write(db, "CP1", "ReserveNow", "in", {"status": "Accepted"})
    rows = list_reservations(tmp_path, now=datetime(2026, 10, 11, tzinfo=timezone.utc))
    assert {r["status"] for r in rows} == {"Requested"}

def test_expired_and_used_are_historical(tmp_path):
    create_current_schema(tmp_path)
    db = tmp_path / DATABASE_FILENAME
    write(db, "CP1", "ReserveNow", "out", {"reservation_id": 42, "connector_id": 0, "id_tag": "ABC",
          "expiry_date": "2026-10-10T14:00:00Z"})
    write(db, "CP1", "ReserveNow", "in", {"status": "Accepted"})
    assert list_reservations(tmp_path, now=datetime(2026, 10, 11, tzinfo=timezone.utc)) == []
    assert list_reservations(tmp_path, include_all=True, now=datetime(2026, 10, 11, tzinfo=timezone.utc))[0]["status"] == "Expired"
    write(db, "CP1", "StartTransaction", "in", {"reservation_id": 42})
    rows = list_reservations(tmp_path, include_all=True, now=datetime(2026, 10, 11, tzinfo=timezone.utc))
    assert rows[0]["status"] == "Used"

def test_reservation_list_cli_parses(parse_cli):
    args = parse_cli("reservation", "list", "--cp", "CP1", "--all", "--since", "7d", "--json")
    assert args.reservation_action == "list"
    assert args.charger == "CP1" and args.all and args.json
