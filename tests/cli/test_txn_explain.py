"""Transaction explanation rules use archive evidence, never charger calls."""
from __future__ import annotations
from types import SimpleNamespace
import pytest
from ocpp_csms.transactions import analysis
from ocpp_csms.cli.txn_explain import parse_explain


def _view(*, state="open", start=True, stop=False, first=100, last=110):
    record = {"start": {"meter_start": first} if start else None,
              "stop": {"meter_stop": last} if stop else None,
              "meter_values": [], "status": state}
    return SimpleNamespace(record=record, status="disconnected" if state == "inferred_stopped" else state,
                           charge_point_id="CP1", connector_id=1, id_tag="ABC",
                           transaction_id=42, unresolved=(), active=not stop and state == "open")


def _analyze(monkeypatch, view, *, overlaps=()):
    class FakeQuery:
        def __init__(self, data_dir):
            pass
        def get(self, transaction_id):
            return view
        def active(self, **filters):
            return [view, *(SimpleNamespace(transaction_id=n) for n in overlaps)]
    monkeypatch.setattr(analysis, "TransactionQuery", FakeQuery)
    monkeypatch.setattr(analysis, "transaction_events", lambda *a: [])
    return analysis.analyze_transaction("/unused", 42)


def test_missing_stop_is_not_assumed_terminal(monkeypatch):
    report = _analyze(monkeypatch, _view())
    finding = next(x for x in report["findings"] if x["code"] == "missing_stop")
    assert finding["certainty"] == "observed"
    assert "unknown" in finding["message"]


def test_recovery_is_explicitly_inferred(monkeypatch):
    report = _analyze(monkeypatch, _view(state="inferred_stopped"))
    assert next(x for x in report["findings"] if x["code"] == "inferred_stop")["certainty"] == "inferred"


def test_completed_transaction_has_stop_evidence(monkeypatch):
    report = _analyze(monkeypatch, _view(stop=True))
    assert report["stop_recorded"]
    assert any(x["code"] == "stop_recorded" for x in report["findings"])


def test_overlapping_open_transactions(monkeypatch):
    report = _analyze(monkeypatch, _view(), overlaps=[43])
    assert any(x["code"] == "overlapping_open_transactions" for x in report["findings"])


def test_decreasing_meter_is_warned_not_explained_as_cause(monkeypatch):
    report = _analyze(monkeypatch, _view(stop=True, first=500, last=200))
    finding = next(x for x in report["findings"] if x["code"] == "meter_decrease")
    assert finding["certainty"] == "observed"
    assert "reset" in finding["message"]


def test_explain_parser_and_context():
    args = parse_explain(["42", "--context", "5", "--json"], data_dir="/data")
    assert (args.transaction_id, args.context, args.json, args.data_dir) == (42, 5, True, "/data")
