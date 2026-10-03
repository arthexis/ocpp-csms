import pytest

from ocpp_csms.app import run_profile


def test_profile_clear_without_filters_requests_clear_all(cli_parser, profile_control, capsys):
    args = cli_parser.parse_args(["profile", "clear"])

    assert run_profile(args) == 0
    assert profile_control.calls == [
        (args.data_dir, {"command": "clear_charging_profile"})
    ]
    assert capsys.readouterr().out.strip() == "Accepted"


def test_profile_clear_maps_protocol_filters(cli_parser, profile_control):
    args = cli_parser.parse_args([
        "profile",
        "clear",
        "--charger",
        "charger-a",
        "--id",
        "7",
        "--connector",
        "1",
        "--purpose",
        "ChargePointMaxProfile",
        "--stack-level",
        "2",
    ])

    assert run_profile(args) == 0
    assert profile_control.calls == [
        (
            args.data_dir,
            {
                "command": "clear_charging_profile",
                "charger": "charger-a",
                "id": 7,
                "connector": 1,
                "purpose": "ChargePointMaxProfile",
                "stack_level": 2,
            },
        )
    ]


@pytest.mark.parametrize(
    "argv",
    [
        ["profile", "clear", "--id", "-1"],
        ["profile", "clear", "--connector", "-1"],
        ["profile", "clear", "--stack-level", "-1"],
    ],
)
def test_profile_clear_rejects_negative_filters_without_contacting_control(
    cli_parser, profile_control, capsys, argv
):
    args = cli_parser.parse_args(argv)

    assert run_profile(args) == 1
    assert profile_control.calls == []
    assert "error:" in capsys.readouterr().out


def test_profile_clear_propagates_rejected_status(cli_parser, profile_control, capsys):
    profile_control.response = {"ok": True, "response": {"status": "Rejected"}}
    args = cli_parser.parse_args(["profile", "clear", "--purpose", "TxProfile"])

    assert run_profile(args) == 1
    assert capsys.readouterr().out.strip() == "Rejected"
