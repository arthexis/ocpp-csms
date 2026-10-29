from ocpp_csms.transactions.export_metadata import transaction_export_metadata


def record(samples, meter_start=100000):
    return {
        "status": "open",
        "start": {"meter_start": meter_start, "timestamp": "2026-10-29T12:00:00Z"},
        "meter_values": [{"meter_value": [
            {"timestamp": stamp, "sampled_value": values} for stamp, values in samples
        ]}],
    }


def test_live_register_wh_and_zero():
    data = record([("2026-10-29T12:01:00Z", [
        {"measurand": "Energy.Active.Import.Register", "value": "134750", "unit": "Wh"}
    ])])
    item = transaction_export_metadata(data)
    assert item["live_energy_wh"] == 34750
    assert item["live_energy_observed_at"] == "2026-10-29T12:01:00Z"
    assert item["live_energy_measurand"] == "Energy.Active.Import.Register"
    data["meter_values"][0]["meter_value"][0]["sampled_value"][0]["value"] = "100000"
    assert transaction_export_metadata(data)["live_energy_wh"] == 0


def test_kwh_time_order_and_invalid():
    data = record([
        ("2026-10-29T12:02:00Z", [{"measurand": "Power.Active.Import", "value": "8000"}]),
        ("2026-10-29T12:03:00Z", [{"measurand": "Energy.Active.Import.Register", "value": "134.75", "unit": "kWh"}]),
        ("2026-10-29T12:01:00Z", [{"measurand": "Energy.Active.Import.Register", "value": "999", "unit": "kWh"}]),
        ("2026-10-29T12:04:00Z", [{"measurand": "Energy.Active.Import.Register", "value": "NaN"}]),
    ])
    assert transaction_export_metadata(data)["live_energy_wh"] == 34750
    assert transaction_export_metadata(data)["live_energy_observed_at"] == "2026-10-29T12:03:00Z"


def test_missing_and_final():
    assert transaction_export_metadata(record([]))["live_energy_reason"] == "missing_sample"
    assert transaction_export_metadata(record([], meter_start=None))["live_energy_reason"] == "missing_start"
    finished = record([])
    finished["stop"] = {"meter_stop": 130000}
    assert "live_energy_wh" not in transaction_export_metadata(finished)
