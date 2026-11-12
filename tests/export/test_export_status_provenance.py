"""Last authoritative status receipt must not follow snapshot export time."""
import json
import sqlite3

from ocpp_csms.export_contract import _attach_status_provenance


def test_latest_status_notification_receipt_without_fabricated_timestamp():
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY,charger_id TEXT,"
                   "received_at TEXT,direction TEXT,action TEXT,payload_json TEXT)")
        for id_, action, timestamp, status in [
            (1, "StatusNotification", "2026-10-29T21:35:00Z", "Faulted"),
            (2, "Heartbeat", "2026-10-30T06:00:00Z", None),
        ]:
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?)",
                       (id_, "charger", timestamp, "in", action,
                        json.dumps({"connector_id": 1, "status": status})))
        status = {"chargers": [{"id": "charger", "connectors": [
            {"id": 1, "status": "Faulted"}, {"id": 2, "status": "Available"}]}]}
        _attach_status_provenance(db, status)
        connectors = status["chargers"][0]["connectors"]
        assert connectors[0]["status_received_at"] == "2026-10-29T21:35:00Z"
        assert "status_received_at" not in connectors[1]
        db.execute("INSERT INTO events VALUES(?,?,?,?,?,?)",
                   (3, "charger", "2026-10-30T06:05:00Z", "in",
                    "StatusNotification", json.dumps({"connector_id": 1, "status": "Available"})))
        _attach_status_provenance(db, status)
        assert connectors[0]["status_received_at"] == "2026-10-30T06:05:00Z"
