"""A4d D1: command registration must retain only intentional maintenance aliases."""
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


@pytest.mark.parametrize("name", ["diag", "diagnostics"])
def test_removed_diagnostic_commands_are_not_registered(name):
    parser, commands = build_parser()
    assert name not in commands
    with pytest.raises(SystemExit) as exc:
        parser.parse_args([name])
    assert exc.value.code == 2


def test_availability_alias_shares_parser():
    _, commands = build_parser()
    assert commands["avail"] is commands["availability"]
