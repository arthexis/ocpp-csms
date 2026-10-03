import pytest

from field.state import FieldConfig, FieldState, save_state


@pytest.fixture
def field_config(tmp_path):
    return FieldConfig(
        charger="charger-a",
        legacy_service="legacy-example.service",
        csms_service="candidate-example.service",
        ocpp_command="/opt/example/bin/ocpp-csms",
        listener_host="127.0.0.1",
        listener_port=12345,
        csms_data_dir=str(tmp_path / "data"),
        control_socket=str(tmp_path / "data" / "control.sock"),
    )


@pytest.fixture
def field_run(tmp_path, field_config):
    def create(*, phase="configuration", watchdog="disabled"):
        run_dir = tmp_path / "run"
        save_state(
            run_dir,
            FieldState(config=field_config, phase=phase, watchdog=watchdog),
        )
        return run_dir

    return create
