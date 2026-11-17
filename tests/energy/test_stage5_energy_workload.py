"""Stage 5: deterministic MeterValues decoding and post-SQL filtering baseline."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from time import perf_counter

from ocpp_csms.energy_query import EnergyQuery
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.schema import database_path
import ocpp_csms.energy_query as energy_module


def test_meter_values_read_amplification_and_utc_filter(tmp_path, monkeypatch, record_property):
    """Bounded synthetic workload; timing is telemetry only, never a CI gate."""
    EventStore(tmp_path)
    base = datetime(2026, 11, 16, 14, 0, tzinfo=timezone.utc)
    rows = []
    for index in range(72):
        charger = "CP-B" if index % 3 == 0 else "CP-A"
        connector = 1 if index % 2 else 2
        at = base + timedelta(minutes=index)
        # Half the charger timestamps are offset ISO, not literal UTC strings.
        stamp = (at.astimezone(timezone(timedelta(hours=-6))).isoformat()
                 if index % 2 else at.isoformat().replace("+00:00", "Z"))
        payload = {
            "connector_id": connector,
            "meter_value": [{"timestamp": stamp, "sampled_value": [
                {"measurand": "Power.Active.Import", "unit": "W", "value": str(index)},
                {"measurand": "Energy.Active.Import.Register", "unit": "Wh",
                 "value": str(500 + index)},
            ]}],
        }
        rows.append(("2026-12-01T00:00:00Z", charger, "MeterValues", "in",
                     json.dumps(payload)))
    # Ensure malformed and unsupported rows are skipped rather than fabricated.
    rows.extend([
        ("2026-12-01T00:00:00Z", "CP-A", "MeterValues", "in", "{bad-json"),
        ("2026-12-01T00:00:00Z", "CP-A", "MeterValues", "in",
         json.dumps({"connector_id": 1, "meter_value": [{"timestamp": "2026-11-16T14:30:00Z",
                      "sampled_value": [{"measurand": "Unknown", "unit": "W", "value": "2"}]}]})),
    ])
    with sqlite3.connect(database_path(tmp_path)) as conn:
        conn.executemany(
            """INSERT INTO events
            (received_at,charger_id,action,direction,payload_json) VALUES (?,?,?,?,?)""",
            rows,
        )

    original_loads = energy_module.json.loads
    decoded = []

    def traced_loads(value, *args, **kwargs):
        decoded.append(value)
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(energy_module.json, "loads", traced_loads)
    start = perf_counter()
    result = EnergyQuery(tmp_path).samples(
        charger="CP-A", connector=1,
        since="2026-11-16T08:20:00-06:00",
        until="2026-11-16T14:40:00Z",
    )
    elapsed_ms = (perf_counter() - start) * 1000
    expected_indices = [i for i in range(20, 41) if i % 3 and i % 2]
    assert [sample.power_w for sample in result] == [float(i) for i in expected_indices]
    assert [sample.energy_wh for sample in result] == [float(500 + i) for i in expected_indices]
    assert [sample.at for sample in result] == [
        (base + timedelta(minutes=i)).isoformat().replace("+00:00", "Z")
        for i in expected_indices
    ]
    assert all(sample.connector_id == 1 and sample.charger_id == "CP-A" for sample in result)
    # SQL currently selects all 48 CP-A MeterValues plus two anomalies before
    # connector/time filtering. The reported sample time is not the receipt time.
    assert len(decoded) == 50
    record_property("meter_values_72_events_elapsed_ms_observed", round(elapsed_ms, 3))
    record_property("meter_payloads_decoded_for_filtered_request", len(decoded))


def test_malformed_sample_timestamp_does_not_become_receive_time(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("CP-A", "MeterValues", {"connector_id": 1, "meter_value": [
        {"timestamp": "invalid", "sampled_value": [
            {"measurand": "Power.Active.Import", "unit": "W", "value": "1200"}]}
    ]})
    assert EnergyQuery(tmp_path).samples(charger="CP-A") == []
