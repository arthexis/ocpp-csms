import json

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
    return {
        "ok": True,
        "response": {
            "status": status,
            "requested_connector": 0,
            "compatibility_fallback": "physical_connectors",
            "schedules": [
                {"connector_id": 1, "response": schedule(1)},
                {"connector_id": 2, "response": schedule(2, "Accepted" if status == "Accepted" else "Rejected")},
            ],
        },
    }


def test_profile_composite_fallback_renders_notice_and_each_schedule(
    cli_parser, profile_control, capsys
):
    profile_control.response = fallback()
    args = cli_parser.parse_args(["profile", "composite"])

    assert run_profile(args) == 0
    output = capsys.readouterr().out
    assert "does not support aggregate composite schedule on connector 0" in output
    assert "Showing physical connectors instead" in output
    assert "Connector 1" in output
    assert "Connector 2" in output
    assert output.count("60000 W") == 2


def test_profile_composite_fallback_json_preserves_structured_payload(
    cli_parser, profile_control, capsys
):
    profile_control.response = fallback()
    args = cli_parser.parse_args(["profile", "composite", "--json"])

    assert run_profile(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["compatibility_fallback"] == "physical_connectors"
    assert [item["connector_id"] for item in payload["schedules"]] == [1, 2]


def test_profile_composite_partial_fallback_renders_evidence_but_fails(
    cli_parser, profile_control, capsys
):
    profile_control.response = fallback("Rejected")
    args = cli_parser.parse_args(["profile", "composite"])

    assert run_profile(args) == 1
    output = capsys.readouterr().out
    assert "Connector 1" in output and "60000 W" in output
    assert "Connector 2" in output and "Rejected" in output
