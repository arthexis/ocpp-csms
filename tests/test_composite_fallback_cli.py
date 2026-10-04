import json

import pytest

from ocpp_csms.app import run_profile


def schedule(connector, status="Accepted", limit=60000):
    response = {"status": status}
    if status == "Accepted":
        response.update({
            "connector_id": connector,
            "schedule_start": "2026-10-04T00:00:00Z",
            "charging_schedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": limit}],
            },
        })
    return response


def fallback(status="Accepted"):
    second_status = "Accepted" if status == "Accepted" else "Rejected"
    return {
        "ok": True,
        "response": {
            "status": status,
            "requested_connector": 0,
            "compatibility_fallback": "physical_connectors",
            "schedules": [
                {"connector_id": 1, "response": schedule(1)},
                {"connector_id": 2, "response": schedule(2, second_status)},
            ],
        },
    }


@pytest.mark.parametrize(
    ("status", "exit_code", "expected_fragments"),
    [
        ("Accepted", 0, ("Connector 1", "Connector 2", "60000 W")),
        ("Rejected", 1, ("Connector 1", "60000 W", "Connector 2", "Rejected")),
    ],
)
def test_profile_composite_fallback_renders_physical_evidence(
    cli_parser, profile_control, capsys, status, exit_code, expected_fragments
):
    profile_control.response = fallback(status)
    args = cli_parser.parse_args(["profile", "composite"])

    assert run_profile(args) == exit_code
    output = capsys.readouterr().out
    assert "does not support aggregate composite schedule on connector 0" in output
    assert "Showing physical connectors instead" in output
    for fragment in expected_fragments:
        assert fragment in output
    if status == "Accepted":
        assert output.count("60000 W") == 2


def test_profile_composite_fallback_json_preserves_structured_payload(
    cli_parser, profile_control, capsys
):
    profile_control.response = fallback()
    args = cli_parser.parse_args(["profile", "composite", "--json"])

    assert run_profile(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["compatibility_fallback"] == "physical_connectors"
    assert payload["requested_connector"] == 0
    assert [item["connector_id"] for item in payload["schedules"]] == [1, 2]
