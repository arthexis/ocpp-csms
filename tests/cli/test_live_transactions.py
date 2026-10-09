"""Regression coverage for live transaction energy and --last selection."""
import json
from pathlib import Path

from ocpp_csms.transaction_cli import format_transactions, format_transaction
from ocpp_csms.transaction_query import TransactionQuery, TransactionView


def record(transaction_id, *, stopped=False, samples=None):
    result = {
        "transaction_id": transaction_id,
        "charge_point_id": "SIM001",
        "status": "stopped" if stopped else "open",
        "start": {
            "timestamp": f"2026-10-09T00:{transaction_id:02d}:00Z",
            "meter_start": 100,
            "connector_id": 1,
            "id_tag": "TEST001",
        },
        "meter_values": [
            {"connector_id": 1, "transaction_id": transaction_id, "meter_value": [{
                "timestamp": f"2026-10-09T00:{transaction_id:02d}:30Z",
                "sampled_value": [
                    {"value": str(value), "unit": unit, "measurand": "Energy.Active.Import.Register"},
                ],
            }]}
            for value, unit in (samples or [])
        ],
    }
    if stopped:
        result["stop"] = {"timestamp": "2026-10-09T00:01:30Z", "meter_stop": 250}
    return result


def test_open_transaction_energy_uses_latest_cumulative_meter_value():
    view = TransactionView(record(2, samples=[(110, "Wh"), (0.15, "kWh")]), Path("2.json"))
    table = format_transactions([view])
    assert "50 Wh" in table
    assert "Energy:       50 Wh (live)" in format_transaction(view)


def test_stopped_energy_still_uses_meter_stop():
    view = TransactionView(record(1, stopped=True, samples=[(140, "Wh")]), Path("1.json"))
    assert "150 Wh" in format_transactions([view])
    assert "(live)" not in format_transaction(view)


def test_open_without_valid_energy_samples_stays_unknown():
    view = TransactionView(record(2), Path("2.json"))
    assert "Energy:" not in format_transaction(view)
    assert "  -" in format_transactions([view])


def test_last_includes_newest_active_transaction(tmp_path):
    directory = tmp_path / "transactions" / "2026-10-09"
    directory.mkdir(parents=True)
    (directory / "1.json").write_text(json.dumps(record(1, stopped=True)))
    (directory / "2.json").write_text(json.dumps(record(2, samples=[(125, "Wh")])))
    query = TransactionQuery(tmp_path)
    assert query.last().transaction_id == 2
    assert query.last().active
    assert query.list(limit=1)[0].transaction_id == 2
