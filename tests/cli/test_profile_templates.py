import json

import pytest

from ocpp_csms.cli.profile import run_profile


def test_profile_templates_outputs_template_name_and_description(cli_parser, capsys):
    args = cli_parser.parse_args(["profile", "templates"])
    assert run_profile(args) == 0
    output = capsys.readouterr().out
    assert "max-power" in output
    assert "watts" in output


def test_profile_help_exposes_parameter_and_ocpp_mapping(cli_parser, capsys):
    args = cli_parser.parse_args(["profile", "help", "max-power"])
    assert run_profile(args) == 0
    output = capsys.readouterr().out
    for fragment in ("max-power", "--watts [watts]", "SetChargingProfile", "connectorId: 0", "ChargePointMaxProfile", "Absolute", "chargingRateUnit: W", "startPeriod: 0", "limit: [watts]"):
        assert fragment in output


def test_profile_help_unknown_template_fails(cli_parser, capsys):
    assert run_profile(cli_parser.parse_args(["profile", "help", "missing"])) == 1
    assert "unknown profile template" in capsys.readouterr().out


def test_profile_send_sends_built_profile_and_reports_acceptance(cli_parser, profile_control, capsys):
    args = cli_parser.parse_args(["--data-dir", "/tmp/csms", "profile", "send", "max-power", "--watts", "60000", "--charger", "charger-a"])
    assert run_profile(args) == 0
    assert capsys.readouterr().out.strip() == "Accepted"
    data_dir, request = profile_control.calls[0]
    assert data_dir == "/tmp/csms"
    assert request["command"] == "set_charging_profile"
    assert request["charger"] == "charger-a"
    assert request["connector"] == 0
    assert request["profile"]["chargingSchedule"]["chargingSchedulePeriod"][0]["limit"] == 60000


def test_profile_send_relies_on_single_charger_inference_when_unspecified(cli_parser, profile_control):
    assert run_profile(cli_parser.parse_args(["profile", "send", "max-power", "--watts", "60000"])) == 0
    assert "charger" not in profile_control.calls[0][1]


def test_profile_send_rejects_nonpositive_watts_without_contacting_control(cli_parser, profile_control, capsys):
    assert run_profile(cli_parser.parse_args(["profile", "send", "max-power", "--watts", "0"])) == 1
    assert profile_control.calls == []
    assert "watts" in capsys.readouterr().out


def test_profile_send_propagates_rejected_status(cli_parser, profile_control, capsys):
    profile_control.response = {"ok": True, "response": {"status": "Rejected"}}
    assert run_profile(cli_parser.parse_args(["profile", "send", "max-power", "--watts", "60000"])) == 1
    assert capsys.readouterr().out.strip() == "Rejected"


def composite_response(periods=None):
    return {"ok": True, "response": {"status": "Accepted", "connector_id": 0, "schedule_start": "2026-10-03T20:00:00Z", "charging_schedule": {"chargingRateUnit": "W", "chargingSchedulePeriod": periods or [{"startPeriod": 0, "limit": 60000}]}}}


def test_profile_composite_uses_bounded_defaults_and_renders_schedule(cli_parser, profile_control, capsys):
    profile_control.response = composite_response()
    args = cli_parser.parse_args(["profile", "composite"])
    assert run_profile(args) == 0
    assert profile_control.calls == [(args.data_dir, {"command": "get_composite_schedule", "connector": 0, "duration": 3600})]
    output = capsys.readouterr().out
    assert "Connector: 0" in output
    assert "Rate unit: W" in output
    assert "60000 W" in output


def test_profile_composite_preserves_multiple_periods_and_overrides(cli_parser, profile_control, capsys):
    profile_control.response = composite_response([{"startPeriod": 0, "limit": 60000}, {"startPeriod": 1800, "limit": 40000, "numberPhases": 3}])
    args = cli_parser.parse_args(["profile", "composite", "--charger", "charger-a", "--connector", "1", "--duration", "7200"])
    assert run_profile(args) == 0
    assert profile_control.calls[0][1] == {"command": "get_composite_schedule", "charger": "charger-a", "connector": 1, "duration": 7200}
    output = capsys.readouterr().out
    assert "+0s" in output and "60000 W" in output
    assert "+1800s" in output and "40000 W" in output and "3 phase(s)" in output


def test_profile_composite_json_outputs_machine_usable_response(cli_parser, profile_control, capsys):
    profile_control.response = composite_response()
    assert run_profile(cli_parser.parse_args(["profile", "composite", "--json"])) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "Accepted"
    assert payload["charging_schedule"]["chargingSchedulePeriod"][0]["limit"] == 60000


@pytest.mark.parametrize("argv", [["profile", "composite", "--connector", "-1"], ["profile", "composite", "--duration", "0"]])
def test_profile_composite_rejects_invalid_bounds_without_contacting_control(cli_parser, profile_control, capsys, argv):
    assert run_profile(cli_parser.parse_args(argv)) == 1
    assert profile_control.calls == []
    assert "error:" in capsys.readouterr().out


def test_bare_profile_prints_subcommand_help(cli_parser, capsys):
    args = cli_parser.parse_args(["profile"])
    assert run_profile(args) == 0

    output = capsys.readouterr().out
    for subcommand in ("templates", "help", "send", "composite", "clear"):
        assert subcommand in output


@pytest.mark.parametrize("legacy", ["list", "set"])
def test_ambiguous_legacy_profile_commands_are_not_kept(cli_parser, legacy):
    with pytest.raises(SystemExit):
        cli_parser.parse_args(["profile", legacy])


def test_profile_composite_c_alias_selects_connector(cli_parser):
    args = cli_parser.parse_args(["profile", "composite", "--c", "2"])
    assert args.connector == 2


def test_profile_composite_rejects_cp_as_connector_alias(cli_parser):
    with pytest.raises(SystemExit):
        cli_parser.parse_args(["profile", "composite", "--cp", "2"])
