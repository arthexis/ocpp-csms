from ocpp_csms.app import build_parser, run_profile
from ocpp_csms.profile_templates import get_profile_template, list_profile_templates


def test_profile_registry_exposes_only_max_power():
    templates = list_profile_templates()
    assert [template.name for template in templates] == ["max-power"]
    template = get_profile_template("max-power")
    assert template is not None
    assert template.parameters == ("--watts [watts]",)
    assert "ChargePointMaxProfile" in template.ocpp_template
    assert "chargingRateUnit: W" in template.ocpp_template
    assert "limit: [watts]" in template.ocpp_template


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
