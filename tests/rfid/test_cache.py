import pytest

import ocpp_csms.rfid_cache as cache_module
from ocpp_csms.events import EventStore
from ocpp_csms.rfid_cache import resolve_rfid_cache
from tests.rfid.helpers import record_list


@pytest.mark.asyncio
async def test_resolve_cache_returns_known_snapshot_for_live_matching_version(monkeypatch, tmp_path):
    store = EventStore(tmp_path)
    record_list(store, "charger-a", 7, "CARD-A")

    async def fake_send(data_dir, request):
        assert request == {"command": "rfid_version"}
        return {
            "ok": True,
            "response": {"charger": "charger-a", "list_version": 7},
        }

    monkeypatch.setattr(cache_module, "send_control", fake_send)

    state = await resolve_rfid_cache(tmp_path)

    assert state is not None
    assert state.charger_id == "charger-a"
    assert state.list_version == 7
    assert state.has_history is True
    assert state.known is True
    assert state.status == "known"
    assert state.snapshot is not None
    assert state.snapshot.entries[0].rfid == "CARD-A"


@pytest.mark.asyncio
async def test_resolve_cache_marks_unrecognized_live_version_unknown(monkeypatch, tmp_path):
    store = EventStore(tmp_path)
    record_list(store, "charger-a", 7)

    async def fake_send(data_dir, request):
        return {
            "ok": True,
            "response": {"charger": "charger-a", "list_version": 9},
        }

    monkeypatch.setattr(cache_module, "send_control", fake_send)

    state = await resolve_rfid_cache(tmp_path)

    assert state is not None
    assert state.has_history is True
    assert state.known is False
    assert state.status == "unknown"
    assert state.snapshot is None


@pytest.mark.asyncio
async def test_resolve_cache_distinguishes_connected_charger_with_no_history(monkeypatch, tmp_path):
    EventStore(tmp_path)

    async def fake_send(data_dir, request):
        return {
            "ok": True,
            "response": {"charger": "charger-a", "list_version": 3},
        }

    monkeypatch.setattr(cache_module, "send_control", fake_send)

    state = await resolve_rfid_cache(tmp_path)

    assert state is not None
    assert state.charger_id == "charger-a"
    assert state.list_version == 3
    assert state.has_history is False
    assert state.known is False
    assert state.status == "no_history"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        {"error": "no_charger_connected"},
        {"error": "charger_required", "chargers": ["charger-a", "charger-b"]},
        {"error": "command_failed", "detail": "NotSupported"},
        {"ok": True, "response": {"charger": "charger-a"}},
        {"ok": True, "response": {"list_version": 7}},
    ],
)
async def test_resolve_cache_returns_none_without_usable_live_state(monkeypatch, tmp_path, result):
    async def fake_send(data_dir, request):
        return result

    monkeypatch.setattr(cache_module, "send_control", fake_send)

    assert await resolve_rfid_cache(tmp_path) is None


@pytest.mark.asyncio
async def test_resolve_cache_does_not_use_stale_history_when_charger_is_disconnected(monkeypatch, tmp_path):
    store = EventStore(tmp_path)
    record_list(store, "charger-a", 7)

    async def fake_send(data_dir, request):
        raise FileNotFoundError("control.sock")

    monkeypatch.setattr(cache_module, "send_control", fake_send)

    assert await resolve_rfid_cache(tmp_path) is None


@pytest.mark.asyncio
async def test_resolve_cache_can_target_explicit_charger(monkeypatch, tmp_path):
    store = EventStore(tmp_path)
    record_list(store, "charger-b", 4, "CARD-B")
    requests = []

    async def fake_send(data_dir, request):
        requests.append(request)
        return {
            "ok": True,
            "response": {"charger": "charger-b", "list_version": 4},
        }

    monkeypatch.setattr(cache_module, "send_control", fake_send)

    state = await resolve_rfid_cache(tmp_path, charger="charger-b")

    assert requests == [{"command": "rfid_version", "charger": "charger-b"}]
    assert state is not None
    assert state.known is True
    assert state.snapshot.entries[0].rfid == "CARD-B"
