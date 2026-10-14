"""Text and JSON txn commands share read-only query selection."""
import json

import pytest

from ocpp_csms.cli.transactions import run_transactions
from ocpp_csms.cli.json_contracts import run_transactions_json
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.transactions.query import TransactionQuery


@pytest.mark.parametrize("selector", [
    (), ("--active",), ("--last",), ("--cp", "cp-a"),
    ("-c", "1"), ("--rfid", "CARD-A"), ("--since", "7D"),
    ("--limit", "1"), ("--no-limit",), ("--local-time",),
])
def test_text_and_json_use_identical_transaction_selection(parse_cli, tmp_path, monkeypatch, capsys, selector):
    store = EventStore(tmp_path)
    txn_id = store.commit_start("cp-a", {
        "connector_id": 1, "id_tag": "CARD-A", "meter_start": 100,
        "timestamp": "2026-10-14T12:00:00Z",
    })
    observed = []
    for method in ("get", "list", "last", "active"):
        original = getattr(TransactionQuery, method)
        def record(self, *args, _original=original, _method=method, **kwargs):
            observed.append((_method, args, kwargs))
            return _original(self, *args, **kwargs)
        monkeypatch.setattr(TransactionQuery, method, record)

    base = ("--data-dir", str(tmp_path), "txn")
    text_args = parse_cli(*base, *selector)
    run_transactions(text_args)
    text_calls = observed[:]
    observed.clear()

    json_args = parse_cli(*base, *selector, "--json")
    assert run_transactions_json(json_args) == 0
    assert observed == text_calls
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "ocpp-csms/transactions/v1"


@pytest.mark.parametrize("selector", [
    ("--active",), ("--last",), ("--no-limit",), ("--limit", "2"),
])
def test_detail_selector_conflict_matches_in_text_and_json(parse_cli, tmp_path, selector):
    base = ("--data-dir", str(tmp_path), "txn", "1", *selector)
    with pytest.raises(ValueError, match="transaction ID cannot be combined"):
        run_transactions(parse_cli(*base))
    with pytest.raises(ValueError, match="transaction ID cannot be combined"):
        run_transactions_json(parse_cli(*base, "--json"))
