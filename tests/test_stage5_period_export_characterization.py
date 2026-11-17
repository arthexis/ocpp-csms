"""Stage 5: baseline for report period selection and export/v1 provenance."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.export_contract import export_contract
import ocpp_csms.export_contract as exporter
from ocpp_csms.reports import build_report
from ocpp_csms.schema import CURRENT_SCHEMA_VERSION, database_path, source_id


def test_report_utc_text_bounds_and_preperiod_start_characterization(tmp_path):
    """Characterize COALESCE(start_received_at, stop_received_at); no policy change."""
    EventStore(tmp_path)
    # 16:00..17:00 UTC. SQL compares the stored 'Z' text to '+00:00'
    # normalized query bounds lexically, not as equal UTC instants.
    # A stop inside the period does not select a row whose
    # non-NULL start receipt was before the period.
    cases = [
        (1, "2026-11-16T15:59:00Z", "2026-11-16T16:30:00Z"),
        (2, "2026-11-16T16:00:00Z", None),
        (3, "2026-11-16T17:00:00Z", None),
        (4, "2026-11-16T17:00:01Z", None),
        (5, None, "2026-11-16T16:20:00Z"),
        (6, "2026-11-16T15:00:00Z", "2026-11-16T18:00:00Z"),
        (7, "2026-11-16T17:00:00+00:00", None),
    ]
    with sqlite3.connect(database_path(tmp_path)) as conn:
        conn.executemany(
            """INSERT INTO transactions
            (transaction_id,charger_id,connector_id,id_tag,meter_start,started_at,
             start_received_at,meter_stop,stopped_at,stop_received_at,state,last_activity_at)
            VALUES (?,'CP-A',1,'CARD',0,?,?,?,? ,?, ?,?)""",
            [(n, start, start, 500 if stop else None, stop, stop,
              "stopped" if stop else "open", stop or start)
             for n, start, stop in cases],
        )
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION

    local = timezone(timedelta(hours=-6))
    local_start = datetime(2026, 11, 16, 10, 0, tzinfo=local)
    local_end = datetime(2026, 11, 16, 11, 0, tzinfo=local)
    utc_start = local_start.astimezone(timezone.utc)
    utc_end = local_end.astimezone(timezone.utc)
    local_report = build_report(tmp_path, since=local_start, until=local_end)
    utc_report = build_report(tmp_path, since=utc_start, until=utc_end)
    assert local_report["schema"] == "ocpp-csms/report/v1"
    actual = [row["transaction_id"] for row in local_report["data"]["transactions"]]
    # Exact upper bound in 'Z' (row 3) is excluded, while the identical
    # instant written '+00:00' (row 7) is included by the current query.
    # Characterize this discrepancy; do not alter production SQL in Stage 5.
    assert actual == [2, 5, 7]
    assert [row["transaction_id"] for row in utc_report["data"]["transactions"]] == actual
    assert local_report["data"]["period"] == utc_report["data"]["period"]
    assert local_report["data"]["subtotal"]["transactions"] == 3
    assert 1 not in actual and 6 not in actual  # started before, stopped during/after
    assert 3 not in actual  # 'Z' representation is textually greater than '+00:00'
    assert [row["transaction_id"] for row in build_report(
        tmp_path, since=utc_start, until=utc_end, charger_id="CP-OTHER"
    )["data"]["transactions"]] == []


def test_export_source_cursor_and_meter_sample_provenance_survive_snapshot(tmp_path, monkeypatch):
    store = EventStore(tmp_path)
    with sqlite3.connect(database_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE appliance_metadata SET value = ? WHERE key = 'source_id'",
            ("fixture-stage5-source",),
        )
    store.record_ocpp("CP-A", "MeterValues", {
        "connector_id": 1,
        "meter_value": [{"timestamp": "2026-11-16T09:01:00-06:00",
                         "sampled_value": [{"measurand": "Power.Active.Import",
                                            "unit": "W", "value": "1200"}]}],
    })
    store.record_ocpp("CP-B", "Authorize", {"id_tag": "CARD"})

    original = exporter._event_rows
    observed = [False]

    def read_then_append(connection, *, after, limit):
        page = original(connection, after=after, limit=limit)
        if not observed[0]:
            observed[0] = True
            store.record_ocpp("CP-A", "BootNotification", {"charge_point_vendor": "Fixture"})
        return page

    monkeypatch.setattr(exporter, "_event_rows", read_then_append)
    first = export_contract(tmp_path, after=0, limit=2)["data"]
    monkeypatch.setattr(exporter, "_event_rows", original)
    retry = export_contract(tmp_path, after=0, limit=2)["data"]
    following = export_contract(tmp_path, after=first["cursor"]["next"], limit=2)["data"]

    assert source_id(tmp_path) == "fixture-stage5-source"
    assert {part["source_id"] for part in (first, retry, following)} == {"fixture-stage5-source"}
    assert first["cursor"] == {"after": 0, "next": 2, "more": False}
    assert [event["id"] for event in first["events"]] == [1, 2]
    assert [event["charger_id"] for event in first["events"]] == ["CP-A", "CP-B"]
    assert [event["id"] for event in following["events"]] == [3]
    assert first["energy"][0]["source_event_id"] == 1
    assert first["energy"][0]["sample_index"] == 0
    assert first["energy"][0]["at"] == "2026-11-16T15:01:00Z"
    assert first["energy"][0]["power_w"] == 1200.0
    assert first["energy"] == retry["energy"]
    assert first["events"] == retry["events"]
    assert first["cursor"]["next"] == retry["cursor"]["next"]
    assert all(event["id"] > first["cursor"]["next"] for event in following["events"])
    assert first["status"]["chargers"]  # no fabricated new boot notification in snapshot
