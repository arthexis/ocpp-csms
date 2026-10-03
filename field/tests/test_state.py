from field.state import FieldState, load_state, save_state


def test_state_round_trip_preserves_configuration(tmp_path, field_config):
    expected = FieldState(config=field_config)

    save_state(tmp_path, expected)

    assert load_state(tmp_path) == expected
    assert list(tmp_path.glob(".state-*")) == []
