from pathlib import Path

from ocpp_forwarder.__main__ import build_parser


def test_forwarder_cli_has_run_once_and_status():
    parser = build_parser()

    run = parser.parse_args(["--satellite-id", "gway-004", "run", "--once"])
    status = parser.parse_args(["--satellite-id", "gway-004", "status"])

    assert run.command == "run"
    assert run.once is True
    assert status.command == "status"


def test_forwarder_defaults_to_ocpp_prefixed_paths():
    args = build_parser().parse_args(["--satellite-id", "gway-004", "status"])

    assert args.collector_url == "https://ocpp-collector.arthexis.com"
    assert args.token_file == "/etc/ocpp-forwarder/token"
    assert args.state_file == "/var/lib/ocpp-forwarder/state.json"
    assert args.csms_command == "/usr/local/bin/ocpp-csms"
