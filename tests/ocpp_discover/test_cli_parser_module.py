"""CLI parser extraction preserves all command-line entry points."""

from ocpp_discover import discover
from ocpp_discover.cli_parser import build_parser


def test_parser_import_remains_compatible():
    assert discover.build_parser is build_parser


def test_parser_defaults_and_run_flags():
    parser = build_parser()
    defaults = parser.parse_args([])
    assert defaults.interface == "eth0"
    assert defaults.seconds == 15.0
    assert defaults.min_requests == 2
    args = parser.parse_args([
        "run", "--data-dir", "/data", "--state-dir", "/state",
        "--existing-endpoint-only", "--passive-capture-log", "/tmp/packets.log",
    ])
    assert args.existing_endpoint_only
    assert args.passive_capture_log == "/tmp/packets.log"
    assert args.listen_port == 9000


def test_cleanup_command_parser():
    args = build_parser().parse_args(["cleanup", "--state-dir", "/state"])
    assert args.command == "cleanup"
    assert args.state_dir == "/state"
