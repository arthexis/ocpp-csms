import json

from ocpp_forwarder.state import ForwarderState, StateStore


def test_missing_state_starts_at_zero(tmp_path):
    store = StateStore(tmp_path / "state.json")

    assert store.load() == ForwarderState()


def test_state_write_is_atomic_and_round_trips(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    state = ForwarderState(
        source_id="source-a",
        cursor=42,
        last_success_at="2026-10-07T20:00:00Z",
    )

    store.save(state)

    assert store.load() == state
    assert not path.with_suffix(".json.tmp").exists()
    assert json.loads(path.read_text())["cursor"] == 42
