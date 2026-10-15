import asyncio
import json
from types import SimpleNamespace

import pytest

from ocpp_csms.control import ControlServer, dispatch_control


class Session:
    def __init__(self):
        self.calls = []

    async def remote_start(self, *, id_tag, connector_id=None):
        self.calls.append(("start", id_tag, connector_id))
        return SimpleNamespace(status="Accepted")

    async def remote_stop(self, transaction_id):
        self.calls.append(("stop", transaction_id))
        return SimpleNamespace(status="Accepted")

    async def reset(self, reset_type="Soft"):
        self.calls.append(("reset", reset_type))
        return SimpleNamespace(status="Accepted")

    async def get_configuration(self, keys=None):
        self.calls.append(("config", keys))
        return SimpleNamespace(configuration_key=[{"key": (keys or ["HeartbeatInterval"])[0], "readonly": False, "value": "60"}], unknown_key=[])

    async def change_configuration(self, key, value):
        self.calls.append(("config_set", key, value))
        return SimpleNamespace(status="Accepted")


class Registry:
    def __init__(self, session=None, active=None, sessions=None):
        if sessions is not None:
            self.sessions = dict(sessions)
        elif session is not None:
            self.sessions = {"charger-a": session}
        else:
            self.sessions = {}
        self.active = active or {}
        self.events = []

    def session(self, charge_point_id):
        return self.sessions.get(charge_point_id)

    def connected_chargers(self):
        return sorted(self.sessions)

    def physical_connector_ids(self, charge_point_id):
        return []

    def active_transactions(self, charge_point_id):
        values = self.active.get(charge_point_id, [])
        return [
            value if isinstance(value, tuple) else (value, None)
            for value in values
        ]

    def active_transaction_ids(self, charge_point_id):
        return [transaction_id for transaction_id, _connector_id in self.active_transactions(charge_point_id)]

    def record_control_event(self, event, *, charger_id, details=None):
        self.events.append({"event": event, "charger_id": charger_id, "details": details or {}})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control_request", "expected_call"),
    [
        ({"command": "start", "charger": "charger-a", "connector": 2, "id_tag": "REMOTE"}, ("start", "REMOTE", 2)),
        ({"command": "stop", "charger": "charger-a", "transaction": 42}, ("stop", 42)),
        ({"command": "reset", "charger": "charger-a"}, ("reset", "Soft")),
        ({"command": "reset", "charger": "charger-a", "type": "Hard"}, ("reset", "Hard")),
    ],
)
async def test_dispatches_supported_commands(control_request, expected_call):
    session = Session()
    response = await dispatch_control(Registry(session), control_request)
    assert response == {"ok": True, "response": {"status": "Accepted"}}
    assert session.calls == [expected_call]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control_request", "expected_call"),
    [
        ({"command": "start", "id_tag": "REMOTE", "connector": 2}, ("start", "REMOTE", 2)),
        ({"command": "stop", "transaction": 42}, ("stop", 42)),
        ({"command": "reset"}, ("reset", "Soft")),
        ({"command": "config", "keys": ["HeartbeatInterval"]}, ("config", ["HeartbeatInterval"])),
    ],
)
async def test_single_connected_charger_is_inferred(control_request, expected_call):
    session = Session()
    response = await dispatch_control(Registry(session), control_request)
    assert response["ok"] is True
    assert session.calls == [expected_call]


@pytest.mark.asyncio
async def test_config_set_changes_then_reads_back_on_inferred_charger():
    session = Session()
    response = await dispatch_control(
        Registry(session),
        {"command": "config_set", "key": "HeartbeatInterval", "value": "60", "force": False},
    )
    assert response["response"]["change"] == {"status": "Accepted"}
    assert response["response"]["readback"]["configuration_key"][0]["value"] == "60"
    assert session.calls == [
        ("config_set", "HeartbeatInterval", "60"),
        ("config", ["HeartbeatInterval"]),
    ]


@pytest.mark.asyncio
async def test_config_set_is_blocked_during_active_transaction():
    session = Session()
    registry = Registry(session, active={"charger-a": [17]})
    response = await dispatch_control(
        registry,
        {"command": "config_set", "key": "HeartbeatInterval", "value": "60"},
    )
    assert response["error"] == "active_transaction"
    assert session.calls == []
    assert registry.events[0]["event"] == "configuration_change_blocked"


@pytest.mark.asyncio
async def test_config_set_force_records_override_and_runs():
    session = Session()
    registry = Registry(session, active={"charger-a": [17]})
    response = await dispatch_control(
        registry,
        {"command": "config_set", "key": "HeartbeatInterval", "value": "60", "force": True},
    )
    assert response["ok"] is True
    assert registry.events[0]["event"] == "configuration_change_forced"
    assert session.calls[0] == ("config_set", "HeartbeatInterval", "60")


@pytest.mark.asyncio
async def test_missing_charger_fails_when_none_are_connected():
    response = await dispatch_control(Registry(), {"command": "reset"})
    assert response == {"error": "no_charger_connected"}


@pytest.mark.asyncio
async def test_missing_charger_requires_selector_when_multiple_are_connected():
    response = await dispatch_control(Registry(sessions={"charger-a": Session(), "charger-b": Session()}), {"command": "reset"})
    assert response == {"error": "charger_required", "chargers": ["charger-a", "charger-b"]}


@pytest.mark.asyncio
async def test_legacy_config_positional_charger_is_recognized():
    session = Session()
    response = await dispatch_control(Registry(session), {"command": "config", "keys": ["charger-a", "HeartbeatInterval"]})
    assert response["ok"] is True
    assert session.calls == [("config", ["HeartbeatInterval"])]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("active", "force", "keys", "expected_call", "expected_error", "records_decision"),
    [
        ({}, False, ["HeartbeatInterval"], ("config", ["HeartbeatInterval"]), None, False),
        ({"charger-a": [17]}, False, None, ("config", None), None, False),
        ({"charger-a": [17]}, True, None, ("config", None), None, False),
        ({"charger-b": [88]}, False, None, ("config", None), None, False),
    ],
)
async def test_configuration_dispatch_respects_active_transaction_policy(active, force, keys, expected_call, expected_error, records_decision):
    session = Session()
    registry = Registry(session, active=active)
    request = {"command": "config", "charger": "charger-a", "force": force}
    if keys is not None:
        request["keys"] = keys
    response = await dispatch_control(registry, request)
    if expected_error is None:
        assert response["ok"] is True
        assert session.calls == [expected_call]
    else:
        assert response["error"] == expected_error
        assert response["transactions"] == [17]
        assert session.calls == []
    assert bool(registry.events) is records_decision
    if records_decision:
        assert registry.events[0]["charger_id"] == "charger-a"
        assert registry.events[0]["details"]["transactions"] == [17]


@pytest.mark.asyncio
async def test_disconnected_explicit_charger_is_not_queued():
    response = await dispatch_control(Registry(), {"command": "reset", "charger": "charger-a"})
    assert response == {"error": "charger_not_connected", "charger": "charger-a"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control_request", "error"),
    [
        ({"charger": "charger-a"}, "missing_command"),
        ({"command": "start"}, "missing_id_tag"),
        ({"command": "start", "charger": ""}, "invalid_charger"),
        ({"command": "start", "charger": "charger-a"}, "missing_id_tag"),
        ({"command": "start", "charger": "charger-a", "id_tag": "REMOTE", "connector": -1}, "invalid_connector"),
        ({"command": "stop", "charger": "charger-a"}, "invalid_transaction"),
        ({"command": "reset", "charger": "charger-a", "type": "Warm"}, "invalid_reset_type"),
        ({"command": "config", "charger": "charger-a", "keys": "HeartbeatInterval"}, "invalid_keys"),
        ({"command": "config", "charger": "charger-a", "force": "yes"}, "invalid_force"),
        ({"command": "config_set", "charger": "charger-a", "key": "", "value": "60"}, "invalid_key"),
        ({"command": "config_set", "charger": "charger-a", "key": "HeartbeatInterval", "value": 60}, "invalid_value"),
        ({"command": "unknown", "charger": "charger-a"}, "unknown_command"),
    ],
)
async def test_rejects_invalid_requests(control_request, error):
    response = await dispatch_control(Registry(Session()), control_request)
    assert response["error"] == error


@pytest.mark.asyncio
async def test_command_failure_is_returned_to_caller():
    class FailingSession(Session):
        async def reset(self, reset_type="Soft"):
            raise OSError("connection lost")
    response = await dispatch_control(Registry(FailingSession()), {"command": "reset", "charger": "charger-a"})
    assert response == {"error": "command_failed", "detail": "connection lost"}


@pytest.mark.asyncio
async def test_unix_socket_accepts_one_json_request(tmp_path):
    session = Session()
    path = tmp_path / "control.sock"
    async with ControlServer(Registry(session), path):
        reader, writer = await asyncio.open_unix_connection(str(path))
        writer.write(json.dumps({"command": "stop", "transaction": 9}).encode() + b"\n")
        await writer.drain()
        response = json.loads(await reader.readline())
        writer.close()
        await writer.wait_closed()
        assert response == {"ok": True, "response": {"status": "Accepted"}}
        assert session.calls == [("stop", 9)]
        assert path.exists()
        assert path.stat().st_mode & 0o777 == 0o660
    assert not path.exists()


@pytest.mark.asyncio
async def test_unix_socket_reports_invalid_json(tmp_path):
    path = tmp_path / "control.sock"
    async with ControlServer(Registry(Session()), path):
        reader, writer = await asyncio.open_unix_connection(str(path))
        writer.write(b"not-json\n")
        await writer.drain()
        response = json.loads(await reader.readline())
        writer.close()
        await writer.wait_closed()
    assert response == {"error": "invalid_json"}


@pytest.mark.asyncio
async def test_now_blocks_start_and_reset_when_transaction_is_active():
    for request in (
        {"command": "start", "charger": "charger-a", "id_tag": "REMOTE"},
        {"command": "reset", "charger": "charger-a"},
    ):
        session = Session()
        response = await dispatch_control(
            Registry(session, active={"charger-a": [17]}),
            request,
        )
        assert response == {
            "error": "active_transaction",
            "charger": "charger-a",
            "transactions": [17],
        }
        assert session.calls == []


@pytest.mark.asyncio
async def test_start_allows_free_connector_when_another_connector_is_active():
    session = Session()
    registry = Registry(session, active={"charger-a": [(17, 1)]})

    response = await dispatch_control(
        registry,
        {"command": "start", "charger": "charger-a", "connector": 2, "id_tag": "REMOTE"},
    )

    assert response["ok"] is True
    assert session.calls == [("start", "REMOTE", 2)]


@pytest.mark.asyncio
async def test_start_blocks_when_requested_connector_is_active():
    session = Session()
    registry = Registry(session, active={"charger-a": [(17, 2), (18, 1)]})

    response = await dispatch_control(
        registry,
        {"command": "start", "charger": "charger-a", "connector": 2, "id_tag": "REMOTE"},
    )

    assert response == {
        "error": "active_transaction",
        "charger": "charger-a",
        "transactions": [17],
    }
    assert session.calls == []


@pytest.mark.asyncio
async def test_start_without_connector_remains_blocked_by_any_active_transaction():
    session = Session()
    registry = Registry(session, active={"charger-a": [(17, 1)]})

    response = await dispatch_control(
        registry,
        {"command": "start", "charger": "charger-a", "id_tag": "REMOTE"},
    )

    assert response["error"] == "active_transaction"
    assert response["transactions"] == [17]
    assert session.calls == []


@pytest.mark.asyncio
async def test_within_times_out_if_transaction_stays_active(monkeypatch):
    session = Session()
    registry = Registry(session, active={"charger-a": [17]})
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("ocpp_csms.control.asyncio.sleep", fake_sleep)

    response = await dispatch_control(
        registry,
        {"command": "start", "charger": "charger-a", "id_tag": "REMOTE", "timing": "within", "seconds": 3},
    )

    assert response == {
        "error": "active_transaction_timeout",
        "charger": "charger-a",
        "transactions": [17],
        "seconds": 3,
    }
    assert sleeps == [1, 1, 1]
    assert session.calls == []


@pytest.mark.asyncio
async def test_stop_after_delays_but_is_not_blocked_by_active_transaction(monkeypatch):
    session = Session()
    registry = Registry(session, active={"charger-a": [42]})
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("ocpp_csms.control.asyncio.sleep", fake_sleep)

    response = await dispatch_control(
        registry,
        {"command": "stop", "charger": "charger-a", "transaction": 42, "timing": "after", "seconds": 4},
    )

    assert response["ok"] is True
    assert sleeps == [4]
    assert session.calls == [("stop", 42)]


@pytest.mark.asyncio
async def test_control_timing_is_required_and_validated():
    session = Session()
    registry = Registry(session)

    assert (await dispatch_control(
        registry,
        {"command": "reset", "charger": "charger-a"},
    ))["error"] == "invalid_timing"

    assert (await dispatch_control(
        registry,
        {"command": "reset", "charger": "charger-a", "timing": "after", "seconds": 0},
    ))["error"] == "invalid_timing_seconds"



@pytest.mark.asyncio
async def test_a6_other_charge_point_activity_never_blocks_free_connector():
    session = Session()
    registry = Registry(session, active={"charger-b": [(71, 1)]})
    response = await dispatch_control(registry, {
        "command": "start", "charger": "charger-a", "connector": 1,
        "id_tag": "REMOTE",
    })
    assert response == {"ok": True, "response": {"status": "Accepted"}}
    assert session.calls == [("start", "REMOTE", 1)]


@pytest.mark.asyncio
async def test_a6_multiple_live_connectors_only_block_requested_occupied_connector():
    session = Session()
    registry = Registry(session, active={"charger-a": [(71, 1), (72, 3)]})
    free = await dispatch_control(registry, {
        "command": "start", "charger": "charger-a", "connector": 2,
        "id_tag": "REMOTE",
    })
    occupied = await dispatch_control(registry, {
        "command": "start", "charger": "charger-a", "connector": 3,
        "id_tag": "REMOTE",
    })
    assert free["ok"] is True
    assert occupied == {
        "error": "active_transaction", "charger": "charger-a",
        "transactions": [72],
    }
    assert session.calls == [("start", "REMOTE", 2)]


@pytest.mark.asyncio
async def test_a6_reconnected_registry_uses_live_state_not_old_transaction_ids():
    session = Session()
    stale_registry = Registry(session, active={"charger-a": [(71, 1)]})
    blocked = await dispatch_control(stale_registry, {
        "command": "start", "charger": "charger-a", "connector": 1,
        "id_tag": "REMOTE",
    })
    assert blocked["error"] == "active_transaction"
    reconnected = Registry(session)
    allowed = await dispatch_control(reconnected, {
        "command": "start", "charger": "charger-a", "connector": 1,
        "id_tag": "REMOTE",
    })
    assert allowed["ok"] is True
    assert session.calls == [("start", "REMOTE", 1)]


class EvidenceRegistry(Registry):
    def __init__(self, statuses, active=None):
        super().__init__(Session(), active=active)
        self.statuses = statuses

    def connector_occupancy(self, charge_point_id):
        return self.statuses


@pytest.mark.asyncio
async def test_historical_open_does_not_block_available_connector():
    registry = EvidenceRegistry({1: "Available"}, active={"charger-a": [(17, 1)]})
    response = await dispatch_control(registry, {
        "command": "start", "charger": "charger-a", "connector": 1,
        "id_tag": "REMOTE",
    })
    assert response["ok"] is True
    assert registry.sessions["charger-a"].calls == [("start", "REMOTE", 1)]


@pytest.mark.asyncio
async def test_unknown_connector_warns_and_sends():
    registry = EvidenceRegistry({})
    response = await dispatch_control(registry, {
        "command": "start", "charger": "charger-a", "connector": 1,
        "id_tag": "REMOTE",
    })
    assert response["ok"] is True
    assert response["warnings"]
    assert registry.sessions["charger-a"].calls == [("start", "REMOTE", 1)]


@pytest.mark.asyncio
async def test_busy_connector_requires_explicit_force():
    registry = EvidenceRegistry({1: "Charging"})
    request = {"command": "start", "charger": "charger-a", "connector": 1,
               "id_tag": "REMOTE"}
    blocked = await dispatch_control(registry, request)
    assert blocked["error"] == "active_transaction"
    assert registry.sessions["charger-a"].calls == []
    sent = await dispatch_control(registry, {**request, "force": True})
    assert sent["ok"] is True
    assert sent["warnings"]
    assert registry.sessions["charger-a"].calls == [("start", "REMOTE", 1)]
    assert registry.events[0]["event"] == "control_occupancy_override"


class OccupancyRegistry(Registry):
    def __init__(self, statuses):
        super().__init__(Session())
        self.statuses = statuses

    def connector_occupancy(self, charge_point_id):
        return dict(self.statuses)

    def physical_connector_ids(self, charge_point_id):
        return [1, 2]


@pytest.mark.asyncio
async def test_unspecified_start_allows_other_free_connector():
    registry = OccupancyRegistry({1: "Charging", 2: "Available"})
    response = await dispatch_control(registry, {"command": "start", "id_tag": "AUTO"})
    assert response["ok"] is True
    assert registry.session("charger-a").calls == [("start", "AUTO", None)]


@pytest.mark.asyncio
async def test_unspecified_start_blocks_when_all_connectors_busy():
    registry = OccupancyRegistry({1: "Charging", 2: "Reserved"})
    response = await dispatch_control(registry, {"command": "start", "id_tag": "AUTO"})
    assert response["error"] == "active_transaction"
    assert registry.session("charger-a").calls == []


@pytest.mark.asyncio
async def test_explicit_busy_start_requires_force_and_records_override():
    registry = OccupancyRegistry({1: "Charging", 2: "Available"})
    request = {"command": "start", "connector": 1, "id_tag": "AUTO"}
    assert (await dispatch_control(registry, request))["error"] == "active_transaction"
    response = await dispatch_control(registry, {**request, "force": True})
    assert response["ok"] is True
    assert registry.events[-1]["event"] == "control_occupancy_override"


@pytest.mark.asyncio
async def test_unknown_connector_start_warns_and_proceeds():
    registry = OccupancyRegistry({1: "Charging"})
    response = await dispatch_control(registry, {"command": "start", "id_tag": "AUTO"})
    assert response["ok"] is True
    assert any("unknown" in warning for warning in response["warnings"])
