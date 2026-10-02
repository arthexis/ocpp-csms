from ocpp_csms.app import build_parser, initialize_storage
from ocpp_csms.events import DATABASE_FILENAME
from ocpp_csms.status import appliance_status


def test_init_command_is_available():
    parser, _ = build_parser()

    args = parser.parse_args(["--data-dir", "/tmp/ocpp-csms-test", "init"])

    assert args.command == "init"


def test_initialize_storage_creates_database_and_archive(tmp_path):
    initialize_storage(str(tmp_path))

    status = appliance_status(tmp_path)
    assert (tmp_path / DATABASE_FILENAME).is_file()
    assert (tmp_path / "transactions").is_dir()
    assert status["database"] == "ok"
    assert status["transactions"] == "ok"


def test_status_remains_read_only_on_empty_directory(tmp_path):
    data = appliance_status(tmp_path)

    assert data["database"] == "missing"
    assert data["transactions"] == "missing"
    assert not (tmp_path / DATABASE_FILENAME).exists()
    assert not (tmp_path / "transactions").exists()
