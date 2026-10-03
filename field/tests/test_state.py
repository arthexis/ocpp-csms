from field.state import FieldConfig, FieldState, load_state, save_state


def config():
    return FieldConfig(
        charger="charger-a",
        legacy_service="legacy-example.service",
        csms_service="candidate-example.service",
        ocpp_command="/opt/example/bin/ocpp-csms",
        listener_host="0.0.0.0",
        listener_port=12345,
        csms_data_dir="/tmp/example-csms-data",
        control_socket="/tmp/example-csms-data/control.sock",
    )


def test_state_round_trip_preserves_configuration(tmp_path):
    expected = FieldState(config=config())

    save_state(tmp_path, expected)

    assert load_state(tmp_path) == expected
    assert list(tmp_path.glob(".state-*")) == []
