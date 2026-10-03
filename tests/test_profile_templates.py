import json

import pytest

from ocpp_csms.app import run_profile
from ocpp_csms.profile_templates import build_profile, get_profile_template, list_profile_templates


def test_profile_registry_exposes_only_max_power():
    templates = list_profile_templates()
    assert [template.name for template in templates] == ["max-power"]
    template = get_profile_template("max-power")
    assert template is not None
    assert template.parameters == ("--watts [watts]",)
    assert "ChargePointMaxProfile" in template.ocpp_template
    assert "chargingRateUnit: W" in template.ocpp_template
    assert "limit: [watts]" in template.ocpp_template


def test_max_power_builder_maps_watts_to_station_wide_profile():
    connector, profile = build_profile("max-power", watts=60000)
    assert connector == 0
    assert profile["chargingProfilePurpose"] == "ChargePointMaxProfile"
    assert profile["chargingProfileKind"] == "Absolute"
    assert profile["stackLevel"] == 0
    assert profile["chargingSchedule"]["chargingRateUnit"] == "W"
    assert profile["chargingSchedule"]["chargingSchedulePeriod"] == [
        {"startPeriod": 0, "limit": 60000}
    ]


@pytest.mark.parametrize("watts", [0, -1, True])
def test_max_power_builder_requires_positive_integer_watts(watts):
    with pytest.raises(ValueError, match="watts"):
        build_profile("max-power", watts=watts)


def test_profile_list_outputs_template_name_and_description(cli_parser, capsys):
    args = cli_parser.parse_args(["profile", "list"])

    assert run_profile(args) == 0
    output = capsys.readouterr().out
    assert "max-power" in output
    assert "watts" in output


def test_profile_help_exposes_parameter_and_ocpp_mapping(cli_parser, capsys):
    args = cli_parser.parse_args(["profile", "help", "max-power"])

    assert run_profile(args) == 0
    output = capsys.readouterr().out
    for semantic_fragment in (
        "max-power",
        "--watts [watts]",
        "SetChargingProfile",
        "connectorId: 0",
        "ChargePointMaxProfile",
        "Absolute",
        "chargingRateUnit: W",
        "startPeriod: 0",
        "limit: [watts]",
    ):
        assert semantic_fragment in output


def test_profile_help_unknown_template_fails(cli_parser, capsys):
    args = cli_parser.parse_args(["profile", "help", "missing"])

    assert run_profile(args) == 1
    assert "unknown profile template" in capsys.readouterr().out


def test_profile_set_sends_built_profile_and_reports_acceptance(
    cli_parser, profile_control, capsys
):
    args = cli_parser.parse_args([
        "--data-dir",
        "/tmp/csms",
        "profile",
        "set",
        "max-power",
        "--watts",
        "60000",
        "--charger",
        "charger-a",
    ])

    assert run_profile(args) == 0
    assert capsys.readouterr().out.strip() == "Accepted"
    data_dir, request = profile_control.calls[0]
    assert data_dir == "/tmp/csms"
    assert request["command"] == "set_charging_profile"
    assert request["charger"] == "charger-a"
    assert request["connector"] == 0
    assert request["profile"]["chargingSchedule"]["chargingSchedulePeriod"][0]["limit"] == 60000


def test_profile_set_relies_on_single_charger_inference_when_unspecified(
    cli_parser, profile_control
):
    args = cli_parser.parse_args(["profile", "set", "max-power", "--watts", "60000"])

    assert run_profile(args) == 0
    assert "charger" not in profile_control.calls[0][1]


def test_profile_set_rejects_nonpositive_watts_without_contacting_control(
    cli_parser, profile_control, capsys
):
    args = cli_parser.parse_args(["profile", "set", "max-power", "--watts", "0"])

    assert run_profile(args) == 1
    assert profile_control.calls == []
    assert "watts" in capsys.readouterr().out


def test_profile_set_propagates_rejected_status(cli_parser, profile_control, capsys):
    profile_control.response = {"ok": True, "response": {"status": "Rejected"}}
    args = cli_parser.parse_args(["profile", "set", "max-power", "--watts", "60000"])

    assert run_profile(args) == 1
    assert capsys.readouterr().out.strip() == "Rejected"


def composite_response(periods=None):
    return {
        "ok": True,
        "response": {
            "status": "Accepted",
            "connector_id": 0,
            "schedule_start": "2026-10-03T20:00:00Z",
            "charging_schedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": periods or [{"startPeriod": 0, "limit": 60000}],
            },
        },
    }


def test_profile_composite_uses_bounded_defaults_and_renders_schedule(
    cli_parser, profile_control, capsys
):
    profile_control.response = composite_response()
    args = cli_parser.parse_args(["profile", "composite"])

    assert run_profile(args) == 0
    assert profile_control.calls == [
        (
            args.data_dir,
            {"command": "get_composite_schedule", "connector": 0, "duration": 3600},
        )
    ]
    output = capsys.readouterr().out
    assert "Connector: 0" in output
    assert "Rate unit: W" in output
    assert "60000 W" in output


def test_profile_composite_preserves_multiple_periods_and_overrides(
    cli_parser, profile_control, capsys
):
    profile_control.response = composite_response([
        {"startPeriod": 0, "limit": 60000},
        {"startPeriod": 1800, "limit": 40000, "numberPhases": 3},
    ])
    args = cli_parser.parse_args([
        "profile",
        "composite",
        "--charger",
        "charger-a",
        "--connector",
        "1",
        "--duration",
        "7200",
    ])

    assert run_profile(args) == 0
    assert profile_control.calls[0][1] == {
        "command": "get_composite_schedule",
        "charger": "charger-a",
        "connector": 1,
        "duration": 7200,
    }
    output = capsys.readouterr().out
    assert "+0s" in output and "60000 W" in output
    assert "+1800s" in output and "40000 W" in output and "3 phase(s)" in output


def test_profile_composite_json_outputs_machine_usable_response(
    cli_parser, profile_control, capsys
):
    profile_control.response = composite_response()
    args = cli_parser.parse_args(["profile", "composite", "--json"])

    assert run_profile(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "Accepted"
    assert payload["charging_schedule"]["chargingSchedulePeriod"][0]["limit"] == 60000


@pytest.mark.parametrize(
    "argv",
    [
        ["profile", "composite", "--connector", "-1"],
        ["profile", "composite", "--duration", "0"],
    ],
)
def test_profile_composite_rejects_invalid_bounds_without_contacting_control(
    cli_parser, profile_control, capsys, argv
):
    args = cli_parser.parse_args(argv)

    assert run_profile(args) == 1
    assert profile_control.calls == []
    assert "error:" in capsys.readouterr().out
