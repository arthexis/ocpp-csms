"""Historical transaction SoC metadata is bounded, read-only and independent of event cursor."""
import json
import sqlite3

import pytest

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.schema import DATABASE_FILENAME
from ocpp_csms.transaction_soc import transaction_soc
from ocpp_csms.cli import build_parser, main


def _sample(value):
    return {"connector_id": 1, "meter_value": [{
        "timestamp": "2026-10-29T22:00:00Z",
        "sampled_value": [{"measurand": "SoC", "unit": "Percent", "value": str(value)}],
    }]}


def test_historical_soc_query_reads_authoritative_evidence_only(tmp_path, monkeypatch, capsys):
    store = EventStore(tmp_path)
    tid = store.commit_start("charger-a", {"connector_id": 1, "id_tag": "PRIVATE",
                                           "meter_start": 1000, "timestamp": "2026-10-29T21:00:00Z"})
    store.record_ocpp("charger-a", "MeterValues", _sample(28), transaction_id=tid)
    store.record_ocpp("charger-a", "MeterValues", _sample(100), transaction_id=tid)
    db = tmp_path / DATABASE_FILENAME
    with sqlite3.connect(db) as conn:
        before = (conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                  conn.execute("SELECT MAX(id) FROM events").fetchone()[0])
    result = transaction_soc(tmp_path, cp="charger-a", transaction=tid)
    assert result["schema"] == "ocpp-csms/transaction-soc/v1"
    assert (result["soc_start"], result["soc_end"]) == (28, 100)
    assert result["provenance"] == "ocpp-meter-values"
    assert "PRIVATE" not in json.dumps(result)
    with sqlite3.connect(db) as conn:
        assert before == (conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                          conn.execute("SELECT MAX(id) FROM events").fetchone()[0])
    with pytest.raises(ValueError, match="not found"):
        transaction_soc(tmp_path, cp="another-cp", transaction=tid)
    monkeypatch.setattr("sys.argv", ["ocpp-csms", "--data-dir", str(tmp_path),
                                    "transaction-soc", "--cp", "charger-a",
                                    "--transaction", str(tid)])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["soc_end"] == 100


def test_missing_samples_never_synthesize_initial_soc(tmp_path):
    store = EventStore(tmp_path)
    tid = store.commit_start("charger-a", {"connector_id": 1, "id_tag": "X",
                                           "meter_start": 0, "timestamp": "2026-10-29T21:00:00Z"})
    assert transaction_soc(tmp_path, cp="charger-a", transaction=tid)["soc_end"] is None
    store.record_ocpp("charger-a", "MeterValues", _sample(47), transaction_id=tid)
    one = transaction_soc(tmp_path, cp="charger-a", transaction=tid)
    assert one["soc_start"] is None and one["soc_end"] == 47
    store.record_ocpp("charger-a", "MeterValues", {
        "connector_id": 1, "meter_value": [{"timestamp": "2026-10-29T22:00:00Z",
            "sampled_value": [{"measurand": "SoC", "unit": "Percent", "value": "900"}]}]},
        transaction_id=tid)
    assert transaction_soc(tmp_path, cp="charger-a", transaction=tid)["soc_end"] == 47
