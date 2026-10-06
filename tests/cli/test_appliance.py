from ocpp_csms.cli.appliance import run_appliance
from ocpp_csms.schema import DATABASE_FILENAME


def test_init_runs_through_cli_module(cli_parser, tmp_path):
    args = cli_parser.parse_args(["--data-dir", str(tmp_path), "init"])

    assert run_appliance(args) == 0
    assert (tmp_path / DATABASE_FILENAME).exists()
    assert (tmp_path / "transactions").is_dir()


def test_serve_parser_preserves_lifecycle_options(cli_parser):
    args = cli_parser.parse_args([
        "--data-dir",
        "/tmp/ocpp-test",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "9100",
        "--log-level",
        "debug",
    ])

    assert args.command == "serve"
    assert args.host == "127.0.0.1"
    assert args.port == 9100
    assert args.log_level == "debug"
