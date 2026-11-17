"""Explicit report selectors preserve legacy/v1 while using UTC half-open windows."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ocpp_csms.cli import build_parser
import ocpp_csms.cli.report as report_cli
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.export_contract import export_contract
from ocpp_csms.reports import build_report, format_report
from ocpp_csms.schema import CURRENT_SCHEMA_VERSION, database_path, source_id


BEGIN = datetime(2026, 11, 16, 16, 0, tzinfo=timezone.utc)
END = datetime(2026, 11, 16, 17, 0, tzinfo=timezone.utc)


def _seed(tmp_path):
    """Fixed v8 evidence: two clock spellings, crossing sessions and stale open."""
    EventStore(tmp_path)
    sessions = [
        # id, charger, start receipt, stop receipt, state
        (1, "CP-A", "2026-11-16T15:30:00Z", "2026-11-16T16:10:00Z", "stopped"),
        (2, "CP-A", "2026-11-16T16:00:00Z", "2026-11-16T17:00:00Z", "stopped"),
        (3, "CP-A", "2026-11-16T16:30:00+00:00", None, "open"),
        (4, "CP-A", "2026-11-16T17:00:00+00:00", None, "open"),
        (5, "CP-A", "2026-11-16T15:00:00Z", None, "open"),
        (6, "CP-A", None, "2026-11-16T16:20:00Z", "stopped"),
        (7, "CP-B", "2026-11-16T16:35:00Z", "2026-11-16T16:59:00Z", "stopped"),
        (8, "CP-A", "2026-11-16T17:00:00Z", None, "open"),
    ]
    events = []
    for txid, charger, started, stopped, state in sessions:
        if started is not None:
            events.append((
                started, charger, txid, "StartTransaction",
                json.dumps({"connector_id": 1, "id_tag": f"CARD-{txid}",
                            "meter_start": 0, "timestamp": started}),
            ))
        if stopped is not None:
            events.append((
                stopped, charger, txid, "StopTransaction",
                json.dumps({"transaction_id": txid, "meter_stop": 1000,
                            "timestamp": stopped}),
            ))
    # A stored meter receipt inside the window must qualify transaction 3.
    events.append((
        "2026-11-16T16:40:00-00:00", "CP-A", 3, "MeterValues",
        json.dumps({"connector_id": 1, "transaction_id": 3, "meter_value": [
            {"timestamp": "2026-11-16T16:40:00Z", "sampled_value": [
                {"measurand": "Energy.Active.Import.Register", "unit": "Wh",
                 "value": "500"}
            ]}
        ]}),
    ))
    # Unlinked evidence and outbound replies do not prove transaction activity.
    events.extend([
        ("2026-11-16T16:05:00Z", "CP-A", 999, "MeterValues",
         json.dumps({"connector_id": 1, "transaction_id": 999, "meter_value": []})),
        ("2026-11-16T16:05:00Z", "CP-A", 5, "Heartbeat", "{}"),
    ])
    with sqlite3.connect(database_path(tmp_path)) as db:
        for txid, charger, started, stopped, state in sessions:
            db.execute(
                """INSERT INTO transactions
                   (transaction_id, charger_id, connector_id, id_tag,
                    meter_start, started_at, start_received_at,
                    meter_stop, stopped_at, stop_received_at,
                    state, last_activity_at)
                   VALUES (?, ?, 1, ?, 0, ?, ?, ?, ?, ?, ?, ?)""",
                (txid, charger, f"CARD-{txid}", started, started,
                 1000 if stopped is not None else None, stopped, stopped,
                 state, stopped or started or "2026-11-16T00:00:00Z"),
            )
        db.executemany(
            """INSERT INTO events
               (received_at,charger_id,transaction_id,action,direction,payload_json)
               VALUES (?, ?, ?, ?, 'in', ?)""",
            events,
        )
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    return sessions


def _ids(data):
    return [item["transaction_id"] for item in data["transactions"]]


@pytest.mark.parametrize(("selection", "expected"), [
    ("start", [2, 3, 7]),
    ("completion", [1, 6, 7]),
    ("activity", [2, 1, 6, 3, 7]),
])
def test_three_selection_modes_use_utc_half_open_bounds(tmp_path, selection, expected):
    _seed(tmp_path)
    report = build_report(tmp_path, since=BEGIN, until=END, selection=selection)
    data = report["data"]
    assert report["schema"] == "ocpp-csms/report/v1"
    assert _ids(data) == expected
    assert data["period"] == {
        "since": BEGIN.isoformat(), "until": END.isoformat(),
        "selection": selection, "end_exclusive": True,
    }
    assert data["subtotal"]["transactions"] == len(expected)
    assert data["subtotal"]["energy_basis"] == "full_sessions_not_period_allocated"
    assert data["subtotal"]["energy_kwh"] == sum(
        1 for tx in expected if tx in {1, 2, 6, 7}
    )
    text = format_report(report)
    assert f"Selection: {selection} [since, until)" in text
    assert "not period allocation" in text
    assert "CP-A" in text


@pytest.mark.parametrize("selection", ("start", "completion", "activity"))
def test_offset_datetime_window_matches_utc_and_excludes_end(tmp_path, selection):
    _seed(tmp_path)
    local = timezone(timedelta(hours=-6))
    local_report = build_report(
        tmp_path, since=BEGIN.astimezone(local), until=END.astimezone(local),
        selection=selection,
    )
    utc_report = build_report(tmp_path, since=BEGIN, until=END, selection=selection)
    assert local_report["data"]["period"] == utc_report["data"]["period"]
    assert _ids(local_report["data"]) == _ids(utc_report["data"])
    assert 4 not in _ids(utc_report["data"])
    assert 8 not in _ids(utc_report["data"])


def test_activity_is_observed_events_not_unbounded_open_session_overlap(tmp_path):
    _seed(tmp_path)
    day_later = build_report(
        tmp_path, since=END, until=END + timedelta(hours=1),
        selection="activity",
    )
    assert _ids(day_later["data"]) == [2, 4, 8]
    # Session 5 has not reported anything in either period; it cannot be
    # silently treated as physically active just because SQLite says "open".
    assert 5 not in _ids(day_later["data"])
    assert 5 not in _ids(build_report(
        tmp_path, since=BEGIN, until=END, selection="activity",
    )["data"])


def test_activity_can_repeat_session_without_double_claiming_period_energy(tmp_path):
    _seed(tmp_path)
    first = build_report(tmp_path, since=BEGIN, until=END, selection="activity")["data"]
    second = build_report(
        tmp_path, since=END, until=END + timedelta(hours=1), selection="activity",
    )["data"]
    assert 2 in _ids(first) and 2 in _ids(second)
    assert first["subtotal"]["energy_basis"] == second["subtotal"]["energy_basis"]
    assert first["subtotal"]["energy_basis"] == "full_sessions_not_period_allocated"


def test_charger_filter_and_unknown_evidence_do_not_cross_identity(tmp_path):
    _seed(tmp_path)
    observed = build_report(
        tmp_path, since=BEGIN, until=END, selection="activity", charger_id="CP-A",
    )
    assert _ids(observed["data"]) == [2, 1, 6, 3]
    assert observed["data"]["charger_filter"] == "CP-A"
    assert 7 not in _ids(observed["data"]) and 999 not in _ids(observed["data"])
    complete = build_report(
        tmp_path, since=BEGIN, until=END, selection="completion", charger_id="CP-B",
    )
    assert _ids(complete["data"]) == [7]


def test_implicit_legacy_and_explicit_legacy_remain_identical(tmp_path):
    _seed(tmp_path)
    implicit = build_report(tmp_path, since=BEGIN, until=END)["data"]
    explicit = build_report(
        tmp_path, since=BEGIN, until=END, selection="legacy",
    )["data"]
    assert _ids(implicit) == _ids(explicit)
    assert implicit["period"] == explicit["period"] == {
        "since": BEGIN.isoformat(), "until": END.isoformat(),
    }
    assert implicit["subtotal"] == explicit["subtotal"]
    assert "energy_basis" not in implicit["subtotal"]
    assert "selection" not in implicit["period"]
    assert format_report({"data": implicit}).splitlines()[0].startswith("OCPP-CSMS report:")


def test_empty_equal_period_invalid_modes_and_reversed_bounds(tmp_path):
    _seed(tmp_path)
    for selection in ("start", "completion", "activity"):
        assert _ids(build_report(
            tmp_path, since=BEGIN, until=BEGIN, selection=selection,
        )["data"]) == []
        with pytest.raises(ValueError, match="--since"):
            build_report(tmp_path, since=END, until=BEGIN, selection=selection)
    with pytest.raises(ValueError, match="Unsupported report selection"):
        build_report(tmp_path, since=BEGIN, until=END, selection="everything")


def test_cli_choice_is_opt_in_and_reaches_report_builder(tmp_path, monkeypatch):
    parser, commands = build_parser()
    assert "report" in commands
    default = parser.parse_args(["report"])
    assert default.selection == "legacy"
    parsed = parser.parse_args([
        "report", "--since", BEGIN.isoformat(), "--until", END.isoformat(),
        "--selection", "completion", "--cp", "CP-A", "--json",
    ])
    assert parsed.selection == "completion"
    assert parsed.cp == "CP-A"
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["report", "--selection", "unknown"])
    assert exc.value.code == 2

    calls = []

    def capture(data_dir, **kwargs):
        calls.append((data_dir, kwargs))
        return {"schema": "ocpp-csms/report/v1", "data": {}}

    monkeypatch.setattr(report_cli, "build_report", capture)
    parsed.data_dir = tmp_path
    report_cli.report_for_args(parsed)
    assert calls[0][0] == tmp_path
    assert calls[0][1]["selection"] == "completion"
    assert calls[0][1]["charger_id"] == "CP-A"


def test_report_selection_is_read_only_and_export_cursor_stays_unchanged(tmp_path):
    _seed(tmp_path)
    initial = export_contract(tmp_path, after=0, limit=2)["data"]
    identity = source_id(tmp_path)
    with sqlite3.connect(database_path(tmp_path)) as db:
        before = (db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
                  db.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    for selection in ("start", "completion", "activity", "legacy"):
        build_report(tmp_path, since=BEGIN, until=END, selection=selection)
    final = export_contract(tmp_path, after=0, limit=2)["data"]
    with sqlite3.connect(database_path(tmp_path)) as db:
        assert before == (
            db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
            db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        )
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    assert source_id(tmp_path) == identity
    assert final["source_id"] == initial["source_id"]
    assert final["cursor"] == initial["cursor"]
    assert final["events"] == initial["events"]
    assert final["energy"] == initial["energy"]
    assert final["transactions"] == initial["transactions"]
