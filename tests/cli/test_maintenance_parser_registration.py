"""Maintenance commands retain only intentional aliases."""
import pytest

from ocpp_csms.cli import build_parser


@pytest.mark.parametrize(("argv", "command"), [
    (["diagnose"], "diagnose"),
    (["diagnose", "--list"], "diagnose"),
    (["trigger", "heartbeat"], "trigger"),
    (["avail", "enable"], "avail"),
    (["availability", "enable"], "availability"),
    (["unlock", "-c", "1"], "unlock"),
    (["firmware", "history"], "firmware"),
    (["config"], "config"),
])
def test_canonical_maintenance_registration(argv, command):
    parser, commands = build_parser()
    assert parser.parse_args(argv).command == command
    assert command in commands



def test_availability_alias_shares_parser():
    _, commands = build_parser()
    assert commands["avail"] is commands["availability"]
