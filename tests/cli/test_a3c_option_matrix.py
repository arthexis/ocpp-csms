"""A3c regression matrix for shared options on remaining command families."""
import pytest

from ocpp_csms.cli import build_parser


@pytest.mark.parametrize("argv,expected", [
    (["trigger", "heartbeat", "--charger", "CP1", "-j"], {"charger": "CP1", "json": True}),
    (["availability", "disable", "--cp", "CP1", "-c", "0"], {"charger": "CP1", "connector": 0}),
    (["unlock", "--cp", "CP1", "--connector", "2"], {"charger": "CP1", "connector": 2}),
    (["capabilities", "--charger", "CP1", "-j"], {"charger": "CP1", "json": True}),
    (["reconcile", "--cp", "CP1", "-c", "1"], {"charger": "CP1", "connector": 1}),
    (["energy", "--cp", "CP1", "-c", "1", "--since", "2026-01-01", "--until", "2026-02-01", "-j"],
     {"charger": "CP1", "connector": 1, "since": "2026-01-01", "until": "2026-02-01", "json": True}),
])
def test_command_option_contract(argv, expected):
    parser, _ = build_parser()
    args = parser.parse_args(argv)
    for name, value in expected.items():
        assert getattr(args, name) == value


def test_availability_rejects_negative_connector():
    from ocpp_csms.cli.maintenance import maintenance_request
    parser, _ = build_parser()
    args = parser.parse_args(["availability", "enable", "-c", "-1"])
    with pytest.raises(ValueError, match="connector must be zero or greater"):
        maintenance_request(args)


def test_energy_rejects_negative_connector_without_database(tmp_path):
    from ocpp_csms.cli.energy import run_energy
    parser, _ = build_parser()
    args = parser.parse_args(["--data-dir", str(tmp_path), "energy", "-c", "-1"])
    with pytest.raises(ValueError, match="-c/--connector must be zero or greater"):
        run_energy(args)
