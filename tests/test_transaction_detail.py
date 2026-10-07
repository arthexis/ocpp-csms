from datetime import datetime, timezone
from pathlib import Path

from ocpp_csms.diagnostics import transaction_events
from ocpp_csms.events import EventStore
from ocpp_csms.transaction_cli import _duration, _energy_wh, _meter_summary, format_transactions
from ocpp_csms.transaction_query import TransactionView


def test_duration_and_energy_are_derived_from_transaction_evidence():
    start = {"meter_start": 1000, "timestamp": "2026-10-03T10:00:00Z"}
    stop = {"meter_stop": 1600, "timestamp": "2026-10-03T10:10:30Z"}

    assert _duration(start["timestamp"], stop["timestamp"]) == "10m 30s"
    assert _energy_wh(start, stop) == 600


def test_invalid_or_regressive_energy_is_not_derived():
    assert _energy_wh({}, {}) is None
    assert _energy_wh({"meter_start": 1000}, {"meter_stop": 900}) is None
    assert _duration("2026-10-03T10:10:00Z", "2026-10-03T10:00:00Z") is None


def test_transaction_list_shows_derived_energy():
    view = TransactionView(
        record={
            "transaction_id": 7,
            "charge_point_id": "charger-a",
            "status": "stopped",
            "id_tag": "card-a",
            "start": {
                "connector_id": 1,
                "meter_start": 1000,
                "timestamp": "2026-10-03T10:00:00Z",
            },
            "stop": {
                "meter_stop": 7420,
                "timestamp": "2026-10-03T11:00:00Z",
            },
        },
        path=Path("transactions/2026-10-03/charger-a-7.json"),
    )

    output = format_transactions([view])

    assert "ENERGY" in output
    assert " C " in f" {output.splitlines()[0]} "
    assert " CP " not in f" {output.splitlines()[0]} "
    assert "6.420 kWh" in output


def test_transaction_list_shows_unknown_energy_when_evidence_is_incomplete():
    view = TransactionView(
        record={
            "transaction_id": 8,
            "charge_point_id": "charger-a",
            "status": "open",
            "id_tag": "card-b",
            "start": {
                "connector_id": 1,
                "meter_start": 7420,
                "timestamp": "2026-10-03T11:05:00Z",
            },
            "stop": None,
        },
        path=Path("transactions/2026-10-03/charger-a-8.json"),
    )

    output = format_transactions([view])
    row = output.splitlines()[1].split()

    assert "ENERGY" in output.splitlines()[0]
    assert "kWh" not in output.splitlines()[1]
    assert row[-2] == "-"


def test_meter_summary_uses_only_explicit_measurand_and_unit_semantics():
    summary = _meter_summary(
        {
            "meter_values": [
                {
                    "meter_value": [
                        {
                            "timestamp": "2026-10-03T10:05:00Z",
                            "sampled_value": [
                                {
                                    "value": "1250",
                                    "measurand": "Energy.Active.Import.Register",
                                    "unit": "Wh",
                                },
                                {
                                    "value": "6900",
                                    "measurand": "Power.Active.Import",
                                    "unit": "W",
                                },
                                {"value": "42"},
                            ],
                        }
                    ]
                }
            ]
        }
    )

    assert summary["messages"] == 1
    assert summary["samples"] == 3
    assert summary["first"] == "2026-10-03T10:05:00Z"
    assert summary["last"] == "2026-10-03T10:05:00Z"
    assert summary["latest_energy"] == ("1250", "Wh")
    assert summary["latest_power"] == ("6900", "W")


def test_ambiguous_meter_samples_are_counted_but_not_interpreted():
    summary = _meter_summary(
        {
            "meter_values": [
                {
                    "meter_value": [
                        {
                            "timestamp": "2026-10-03T10:05:00Z",
                            "sampled_value": [{"value": "1250"}],
                        }
                    ]
                }
            ]
        }
    )

    assert summary["samples"] == 1
    assert summary["latest_energy"] is None
    assert summary["latest_power"] is None


def test_transaction_events_returns_only_events_for_requested_transaction(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "StartTransaction", {}, transaction_id=7)
    store.record_ocpp("charger-a", "MeterValues", {}, transaction_id=7)
    store.record_ocpp("charger-a", "Heartbeat", {}, transaction_id=None)
    store.record_ocpp("charger-a", "StopTransaction", {}, transaction_id=8)

    rows = transaction_events(tmp_path, 7)

    assert [row["action"] for row in rows] == ["StartTransaction", "MeterValues"]
    assert {row["transaction_id"] for row in rows} == {7}
