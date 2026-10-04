from __future__ import annotations

import field.smart_charging_probe as probe


def test_request_for_includes_explicit_unit_and_optional_charger():
    request = probe.request_for(probe.PROBES[0], "charger-a")

    assert request == {
        "command": "get_composite_schedule",
        "connector": 0,
        "duration": 3600,
        "charging_rate_unit": "W",
        "charger": "charger-a",
    }


def test_matrix_stops_on_first_accepted_response(monkeypatch):
    calls = []
    responses = iter(
        [
            {"ok": True, "response": {"status": "Rejected"}},
            {
                "ok": True,
                "response": {
                    "status": "Accepted",
                    "connector_id": 1,
                    "charging_schedule": {
                        "chargingRateUnit": "W",
                        "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 60000}],
                    },
                },
            },
            {"ok": True, "response": {"status": "Accepted"}},
        ]
    )

    def fake_send_control(path, request):
        calls.append((path, request))
        return next(responses)

    monkeypatch.setattr(probe, "send_control", fake_send_control)

    results = probe.run_matrix("/tmp/control.sock", charger="charger-a")

    assert [result["probe"] for result in results] == ["A", "B"]
    assert [result["status"] for result in results] == ["Rejected", "Accepted"]
    assert len(calls) == 2
    assert calls[1][1]["connector"] == 1
    assert calls[1][1]["charging_rate_unit"] == "W"


def test_matrix_can_run_all_probes(monkeypatch):
    calls = []

    def fake_send_control(path, request):
        calls.append(request)
        return {"ok": True, "response": {"status": "Rejected"}}

    monkeypatch.setattr(probe, "send_control", fake_send_control)

    results = probe.run_matrix("/tmp/control.sock", stop_on_accept=False)

    assert len(results) == len(probe.PROBES)
    assert [request["charging_rate_unit"] for request in calls] == ["W", "W", "W", "W", "W", "A"]
    assert [(request["connector"], request["duration"]) for request in calls] == [
        (0, 3600),
        (1, 3600),
        (2, 3600),
        (0, 60),
        (0, 300),
        (0, 300),
    ]
