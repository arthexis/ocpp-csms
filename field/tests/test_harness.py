from argparse import Namespace

import pytest

from field.harness import config_from_args, preflight, start_run
from field.state import FieldConfig, load_state


class Probe:
    def __init__(self, *, service=True, executable=True, directory=True):
        self.service = service
        self.executable = executable
        self.directory = directory

    def executable_exists(self, command):
        return self.executable

    def service_exists(self, service):
        return self.service

    def directory_ready(self, path):
        return self.directory


def args(tmp_path, **overrides):
    values = {
        "run_dir": str(tmp_path / "run"),
        "charger": "charger-a",
        "legacy_service": "legacy-example.service",
        "csms_service": "candidate-example.service",
        "ocpp_command": "/opt/example/bin/ocpp-csms",
        "listener_host": "127.0.0.1",
        "listener_port": 12345,
        "csms_data_dir": str(tmp_path / "data"),
        "control_socket": str(tmp_path / "data" / "control.sock"),
        "idle_confirmed": True,
    }
    values.update(overrides)
    return Namespace(**values)


def test_operational_values_come_from_arguments(tmp_path):
    parsed = args(tmp_path)

    config = config_from_args(parsed)

    assert config == FieldConfig(
        charger="charger-a",
        legacy_service="legacy-example.service",
        csms_service="candidate-example.service",
        ocpp_command="/opt/example/bin/ocpp-csms",
        listener_host="127.0.0.1",
        listener_port=12345,
        csms_data_dir=str(tmp_path / "data"),
        control_socket=str(tmp_path / "data" / "control.sock"),
    )


@pytest.mark.parametrize(
    ("overrides", "probe", "failed_check"),
    [
        ({"idle_confirmed": False}, Probe(), "idle_confirmed"),
        ({}, Probe(service=False), "legacy_service_exists"),
        ({}, Probe(executable=False), "ocpp_command_exists"),
        ({}, Probe(directory=False), "csms_data_dir_ready"),
        ({"listener_port": 0}, Probe(), "listener_port_valid"),
        ({"csms_service": "legacy-example.service"}, Probe(), "service_names_distinct"),
    ],
)
def test_preflight_reports_failed_invariants(tmp_path, overrides, probe, failed_check):
    parsed = args(tmp_path, **overrides)

    result = preflight(config_from_args(parsed), idle_confirmed=parsed.idle_confirmed, probe=probe)

    assert result["ok"] is False
    assert result["checks"][failed_check] is False


def test_successful_start_records_preflight_state(tmp_path):
    parsed = args(tmp_path)

    assert start_run(parsed, Probe()) == 0
    state = load_state(tmp_path / "run")
    assert state.phase == "preflight"
    assert (tmp_path / "run" / "preflight.json").exists()


def test_existing_run_refuses_configuration_change(tmp_path):
    assert start_run(args(tmp_path), Probe()) == 0

    with pytest.raises(ValueError):
        start_run(args(tmp_path, listener_port=54321), Probe())
