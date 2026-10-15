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


def test_cli_uses_installed_service_schema_even_when_stopped(tmp_path, monkeypatch):
    from pathlib import Path
    import ocpp_csms.cli as cli

    unit = tmp_path / "ocpp-csms.service"
    selected = tmp_path / "schemas" / "8"
    unit.write_text(
        "[Service]\\nExecStart=/opt/csms/bin/ocpp-csms --data-dir "
        + str(selected) + " serve --host 0.0.0.0 --port 9000\\n"
    )
    monkeypatch.setattr(cli, "Path", lambda value: unit if value == "/etc/systemd/system/ocpp-csms.service" else Path(value))
    assert cli.build_parser()[0].parse_args(["status"]).data_dir == str(selected)
    assert cli.build_parser()[0].parse_args(["events"]).data_dir == str(selected)
    assert cli.build_parser()[0].parse_args(["--data-dir", str(tmp_path), "status"]).data_dir == str(tmp_path)


def test_cli_uses_legacy_default_when_no_unit(tmp_path, monkeypatch):
    from pathlib import Path
    import ocpp_csms.cli as cli

    monkeypatch.setattr(cli, "Path", lambda value: tmp_path / "missing.service" if value == "/etc/systemd/system/ocpp-csms.service" else Path(value))
    assert cli.build_parser()[0].parse_args(["status"]).data_dir == str(cli.default_data_dir())


def test_cli_ignores_malformed_service_selection(tmp_path, monkeypatch):
    from pathlib import Path
    import ocpp_csms.cli as cli

    unit = tmp_path / "ocpp-csms.service"
    unit.write_text("[Service]\\nExecStart=/opt/bin/ocpp-csms --data-dir relative/path serve\\n")
    monkeypatch.setattr(cli, "Path", lambda value: unit if value == "/etc/systemd/system/ocpp-csms.service" else Path(value))
    assert cli.build_parser()[0].parse_args(["status"]).data_dir == str(cli.default_data_dir())
