"""CLI-level regression tests for the compact and verbose event timeline."""
import argparse
import json
from datetime import datetime, timedelta, timezone

import pytest

from ocpp_csms.cli import diagnostics as cli
from ocpp_csms.evidence.diagnostics import format_events
from ocpp_csms.evidence.contracts import events_contract, raw_events_contract


def event(n, action="Heartbeat", payload=None, *, direction="in", charger="CP1",
          transaction=None, tag=None, kind="ocpp"):
    return {
        "id": n, "occurred_at": f"2026-10-09T14:00:{n:02d}Z",
        "charger_id": charger, "kind": kind, "action": action,
        "direction": direction, "transaction_id": transaction, "id_tag": tag,
        "payload": json.dumps(payload if payload is not None else {}),
    }


def args(**overrides):
    defaults = dict(data_dir="unused", charger=None, transaction=None, since=None,
                    until=None, limit=100, json=False, raw=False, verbose=False)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)



@pytest.fixture
def captured_queries(monkeypatch):
    """Capture event query arguments independently of rendering and storage."""
    queries = []
    monkeypatch.setattr(cli, "events_between", lambda *_args, **kwargs: queries.append(kwargs) or [])
    return queries


def query_events(captured_queries, **options):
    cli.run_events(args(**options))
    return captured_queries[-1]

def test_parser_accepts_verbose():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    cli.add_diagnostic_commands(sub)
    parsed = parser.parse_args(["events", "CP1", "--verbose", "--limit", "2"])
    assert parsed.command == "events"
    assert parsed.charger == "CP1"
    assert parsed.verbose is True
    assert parsed.limit == "2"


def test_cli_modes_use_identical_retrieval_and_limit(monkeypatch, capsys):
    observed = []
    stored = [event(1), event(2), event(3)]
    def retrieve(*_args, **kwargs):
        observed.append(kwargs)
        return stored[-kwargs["limit"]:]
    monkeypatch.setattr(cli, "events_between", retrieve)
    cli.run_events(args(limit=2))
    compact = capsys.readouterr().out
    cli.run_events(args(limit=2, verbose=True))
    verbose = capsys.readouterr().out
    assert observed[0] == observed[1]
    assert "×2" in compact
    assert verbose.count("payload=") == 2
    assert "14:00:01Z" not in verbose


def test_verbose_preserves_response_payload_and_transaction(monkeypatch, capsys):
    rows = [event(1, "StartTransaction", {"connector_id": 2, "meter_start": 30},
                  transaction=45, tag="ABC"),
            event(2, "Heartbeat", {"current_time": "2026-10-09T14:00:02Z"},
                  direction="out")]
    monkeypatch.setattr(cli, "events_between", lambda *_a, **_k: rows)
    cli.run_events(args(verbose=True))
    output = capsys.readouterr().out
    assert output.count("payload=") == 2
    assert "tx=45" in output and "RFID=ABC" in output
    assert "[ocpp out]" in output
    assert '"current_time"' in output


def test_json_modes_keep_original_contract(monkeypatch, capsys):
    rows = [event(1, "Heartbeat"), event(2, "Heartbeat")]
    monkeypatch.setattr(cli, "events_between", lambda *_a, **_k: rows)
    cli.run_events(args(json=True))
    assert json.loads(capsys.readouterr().out) == events_contract(rows)
    cli.run_events(args(json=True, raw=True))
    assert json.loads(capsys.readouterr().out) == raw_events_contract(rows)


@pytest.mark.parametrize("options", [
    {"json": True, "verbose": True}, {"raw": True},
    {"limit": 0}, {"transaction": -1},
])
def test_invalid_options_rejected_before_retrieval(monkeypatch, options):
    def forbidden(*_a, **_k):
        pytest.fail("invalid options must not retrieve events")
    monkeypatch.setattr(cli, "events_between", forbidden)
    with pytest.raises(ValueError):
        cli.run_events(args(**options))


def test_emergency_stop_without_transaction_and_recovery():
    rows = [
        event(1, "StatusNotification", {"connector_id": 1, "status": "Faulted",
              "error_code": "InternalError", "info": "EmergencyStop"}),
        event(2, "StatusNotification", {"connector_id": 1, "status": "Available",
              "error_code": "NoError"}),
    ]
    compact = format_events(rows)
    verbose = format_events(rows, verbose=True)
    assert "Faulted InternalError EmergencyStop" in compact
    assert "Available" in compact
    assert "×" not in compact
    assert verbose.count("payload=") == 2


def test_runtime_interrupts_group_and_transaction_is_visible():
    rows = [event(1), event(2, "service_restarted", kind="runtime"),
            event(3, "StartTransaction", {"connector_id": 1}, transaction=7),
            event(4)]
    compact = format_events(rows)
    assert "×" not in compact
    assert "service restarted" in compact
    assert "tx=7" in compact


def test_filters_are_forwarded_unchanged(monkeypatch, capsys):
    captured = []
    def retrieve(*_a, **kwargs):
        captured.append(kwargs)
        return []
    monkeypatch.setattr(cli, "events_between", retrieve)
    cli.run_events(args(charger="CP2", transaction=11,
                        since="2026-10-09T00:00:00Z",
                        until="2026-10-09T23:59:59Z", limit=4))
    capsys.readouterr()
    assert captured == [dict(charger_id="CP2", transaction_id=11,
                             since="2026-10-09T00:00:00+00:00",
                             until="2026-10-09T23:59:59+00:00", limit=4)]


@pytest.mark.parametrize("duration,seconds", [
    ("3d", 3 * 86400), ("12H", 12 * 3600), ("30m", 1800),
    ("1w", 7 * 86400), ("1.5h", 5400),
])
def test_relative_since_uses_current_utc(captured_queries, duration, seconds):
    before = datetime.now(timezone.utc)
    query = query_events(captured_queries, since=duration)
    after = datetime.now(timezone.utc)
    actual = datetime.fromisoformat(query["since"])
    assert before - timedelta(seconds=seconds) <= actual <= after - timedelta(seconds=seconds)
    assert query["limit"] == 100


def test_relative_bounds_keep_common_reference_time(captured_queries):
    query = query_events(captured_queries, since="3d", until="1d")
    since = datetime.fromisoformat(query["since"])
    until = datetime.fromisoformat(query["until"])
    assert until - since == timedelta(days=2)


@pytest.mark.parametrize("bounds", [
    {"since": "banana"}, {"until": "2months"},
    {"since": "1d", "until": "3d"},
    {"since": "2026-10-10T00:00:00Z", "until": "2026-10-09T00:00:00Z"},
])
def test_invalid_or_reversed_time_bounds_do_not_query(monkeypatch, bounds):
    monkeypatch.setattr(cli, "events_between", lambda *_a, **_kw: pytest.fail("unexpected query"))
    with pytest.raises(ValueError):
        cli.run_events(args(**bounds))


@pytest.mark.parametrize("duration,seconds", [
    ("1d", 86400), ("72h", 72 * 3600), ("1W", 7 * 86400),
])
def test_duration_limit_has_no_count_cap(captured_queries, duration, seconds):
    before = datetime.now(timezone.utc)
    query = query_events(captured_queries, limit=duration)
    after = datetime.now(timezone.utc)
    actual = datetime.fromisoformat(query["since"])
    assert before - timedelta(seconds=seconds) <= actual <= after - timedelta(seconds=seconds)
    assert query["limit"] is None


def test_duration_window_ends_at_explicit_until(captured_queries):
    query = query_events(captured_queries, until="2026-10-09T12:00:00Z", limit="1d")
    assert query["since"] == "2026-10-08T12:00:00+00:00"
    assert query["until"] == "2026-10-09T12:00:00+00:00"


def test_duration_limit_intersects_since(captured_queries):
    before = datetime.now(timezone.utc)
    query = query_events(captured_queries, since="1h", limit="1d")
    after = datetime.now(timezone.utc)
    assert before - timedelta(hours=1) <= datetime.fromisoformat(query["since"]) <= after - timedelta(hours=1)
    assert query["limit"] is None


@pytest.mark.parametrize("limit", ["0", "0h", "-1", "nonsense", "2months"])
def test_invalid_event_limits(monkeypatch, limit):
    monkeypatch.setattr(cli, "events_between", lambda *_a, **_kw: pytest.fail("unexpected query"))
    with pytest.raises(ValueError, match="--limit"):
        cli.run_events(args(limit=limit))


def test_short_limit_alias_matches_long_flag(monkeypatch):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    cli.add_diagnostic_commands(sub)
    recorded = []
    monkeypatch.setattr(cli, "events_between", lambda *_a, **kw: recorded.append(kw) or [])
    for value in ("50", "1d", "1.5h"):
        for flag in ("-n", "--limit"):
            parsed = parser.parse_args(["events", flag, value])
            parsed.data_dir = "unused"
            cli.run_events(parsed)
        short, long = recorded[-2:]
        assert short["limit"] == long["limit"]
        if short["since"] is None:
            assert long["since"] is None
        else:
            assert abs((datetime.fromisoformat(short["since"]) - datetime.fromisoformat(long["since"])).total_seconds()) < 1


def test_parser_default_limit_is_100():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    cli.add_diagnostic_commands(sub)
    assert parser.parse_args(["events"]).limit == "100"


def test_no_limit_aliases_disable_count_cap(captured_queries):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    cli.add_diagnostic_commands(sub)
    for flag in ("--no-limit", "-N"):
        parsed = parser.parse_args(["events", "--since", "3d", flag])
        parsed.data_dir = "unused"
        cli.run_events(parsed)
        assert captured_queries[-1]["limit"] is None
        assert captured_queries[-1]["since"] is not None


def test_no_limit_conflicts_with_explicit_limit():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    cli.add_diagnostic_commands(sub)
    for explicit in ("--limit", "-n"):
        with pytest.raises(SystemExit):
            parser.parse_args(["events", explicit, "10", "--no-limit"])


def test_no_limit_with_json_preserves_underlying_rows(monkeypatch, capsys):
    rows = [event(n) for n in range(1, 4)]
    observed = []

    def retrieve(*_args, **kwargs):
        observed.append(kwargs)
        return rows

    monkeypatch.setattr(cli, "events_between", retrieve)
    cli.run_events(args(no_limit=True, json=True, since="3d"))
    assert observed[-1]["limit"] is None
    assert json.loads(capsys.readouterr().out) == events_contract(rows)


def test_no_limit_without_time_filter_queries_all_events(captured_queries):
    query = query_events(captured_queries, no_limit=True)
    assert query["limit"] is None
    assert query["since"] is None
    assert query["until"] is None
