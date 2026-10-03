import pytest

import ocpp_csms.app as app
from ocpp_csms.app import build_parser, run_profile
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
    periods = profile["chargingSchedule"]["chargingSchedulePeriod"]
    assert periods == [{"startPeriod": 0, "limit": 60000}]


@pytest.mark.parametrize("watts", [0, -1, True])
def test_max_power_builder_requires_positive_integer_watts(watts):
    with pytest.raises(ValueError, match="watts"):
        build_profile("max-power", watts=watts)


def test_profile_list_outputs_template_name_and_description(capsys):
    parser, _ = build_parser()
    args = parser.parse_args(["profile", "list"])
    assert run_profile(args) == 0
    output = capsys.readouterr().out
    assert "max-power" in output
    assert "watts" in output


def test_profile_help_exposes_parameter_and_ocpp_mapping(capsys):
    parser, _ = build_parser()
    args = parser.parse_args(["profile", "help", "max-power"])
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


def test_profile_help_unknown_template_fails(capsys):
    parser, _ = build_parser()
    args = parser.parse_args(["profile", "help", "missing"])
    assert run_profile(args) == 1
    assert "unknown profile template" in capsys.readouterr().out


def test_profile_set_sends_built_profile_and_reports_acceptance(monkeypatch, capsys):
    sent = []

    async def fake_send_control(data_dir, request):
        sent.append((data_dir, request))
        return {"ok": True, "response": {"status": "Accepted"}}

    monkeypatch.setattr(app, "send_control", fake_send_control)
    parser, _ = build_parser()
    args = parser.parse_args([
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
    data_dir, request = sent[0]
    assert data_dir == "/tmp/csms"
    assert request["command"] == "set_charging_profile"
    assert request["charger"] == "charger-a"
    assert request["connector"] == 0
    assert request["profile"]["chargingSchedule"]["chargingSchedulePeriod"][0]["limit"] == 60000


def test_profile_set_relies_on_single_charger_inference_when_unspecified(monkeypatch):
    sent = []

    async def fake_send_control(data_dir, request):
        sent.append(request)
        return {"ok": True, "response": {"status": "Accepted"}}

    monkeypatch.setattr(app, "send_control", fake_send_control)
    parser, _ = build_parser()
    args = parser.parse_args(["profile", "set", "max-power", "--watts", "60000"])

    assert run_profile(args) == 0
    assert "charger" not in sent[0]


def test_profile_set_rejects_nonpositive_watts_without_contacting_control(monkeypatch, capsys):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("control should not be contacted")

    monkeypatch.setattr(app, "send_control", fail_if_called)
    parser, _ = build_parser()
    args = parser.parse_args(["profile", "set", "max-power", "--watts", "0"])

    assert run_profile(args) == 1
    assert "watts" in capsys.readouterr().out


def test_profile_set_propagates_rejected_status(monkeypatch, capsys):
    async def fake_send_control(data_dir, request):
        return {"ok": True, "response": {"status": "Rejected"}}

    monkeypatch.setattr(app, "send_control", fake_send_control)
    parser, _ = build_parser()
    args = parser.parse_args(["profile", "set", "max-power", "--watts", "60000"])

    assert run_profile(args) == 1
    assert capsys.readouterr().out.strip() == "Rejected"
