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

    async def get_local_list_version(self):
        self.calls.append(("rfid_version",))
        return SimpleNamespace(list_version=3)

    async def send_local_list(self, list_version, entries):
        self.calls.append(("rfid_send", list_version, entries))
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
        self.rfid_lists = []

    def session(self, charge_point_id):
        return self.sessions.get(charge_point_id)

    def connected_chargers(self):
        return sorted(self.sessions)

    def physical_connector_ids(self, charge_point_id):
        return []

    def active_transaction_ids(self, charge_point_id):
        return list(self.active.get(charge_point_id, []))

    def record_control_event(self, event, *, charger_id, details=None):
        self.events.append({"event": event, "charger_id": charger_id, "details": details or {}})

    def latest_rfid_list_version(self, charger_id):
        versions = [
            item["list_version"]
            for item in self.rfid_lists
            if item["charger_id"] == charger_id
        ]
        return max(versions) if versions else None

    def record_rfid_list(
        self,
        charger_id,
        *,
        list_version,
        entries,
        source_file,
        list_hash,
        verified_version,
    ):
        self.rfid_lists.append(
            {
                "charger_id": charger_id,
                "list_version": list_version,
                "entries": entries,
                "source_file": source_file,
                "list_hash": list_hash,
                "verified_version": verified_version,
            }
        )
        return len(self.rfid_lists)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control_request", "expected_call"),
    [
        ({"command": "start", "charger": "charger-a", "connector": 2, "id_tag": "REMOTE", "timing": "now"}, ("start", "REMOTE", 2)),
        ({"command": "stop", "charger": "charger-a", "transaction": 42, "timing": "now"}, ("stop", 42)),
        ({"command": "reset", "charger": "charger-a", "timing": "now"}, ("reset", "Soft")),
        ({"command": "reset", "charger": "charger-a", "type": "Hard", "timing": "now"}, ("reset", "Hard")),
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
        ({"command": "start", "id_tag": "REMOTE", "connector": 2, "timing": "now"}, ("start", "REMOTE", 2)),
        ({"command": "stop", "transaction": 42, "timing": "now"}, ("stop", 42)),
        ({"command": "reset", "timing": "now"}, ("reset", "Soft")),
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
    response = await dispatch_control(Registry(), {"command": "reset", "timing": "now"})
    assert response == {"error": "no_charger_connected"}


@pytest.mark.asyncio
async def test_missing_charger_requires_selector_when_multiple_are_connected():
    response = await dispatch_control(Registry(sessions={"charger-a": Session(), "charger-b": Session()}), {"command": "reset", "timing": "now"})
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
        ({"charger-a": [17]}, False, None, None, "active_transaction", True),
        ({"charger-a": [17]}, True, None, ("config", None), None, True),
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
    response = await dispatch_control(Registry(), {"command": "reset", "charger": "charger-a", "timing": "now"})
    assert response == {"error": "charger_not_connected", "charger": "charger-a"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control_request", "error"),
    [
        ({"charger": "charger-a"}, "missing_command"),
        ({"command": "start", "timing": "now"}, "missing_id_tag"),
        ({"command": "start", "charger": "", "timing": "now"}, "invalid_charger"),
        ({"command": "start", "charger": "charger-a", "timing": "now"}, "missing_id_tag"),
        ({"command": "start", "charger": "charger-a", "id_tag": "REMOTE", "connector": -1, "timing": "now"}, "invalid_connector"),
        ({"command": "stop", "charger": "charger-a", "timing": "now"}, "invalid_transaction"),
        ({"command": "reset", "charger": "charger-a", "type": "Warm", "timing": "now"}, "invalid_reset_type"),
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
    response = await dispatch_control(Registry(FailingSession()), {"command": "reset", "charger": "charger-a", "timing": "now"})
    assert response == {"error": "command_failed", "detail": "connection lost"}


@pytest.mark.asyncio
async def test_unix_socket_accepts_one_json_request(tmp_path):
    session = Session()
    path = tmp_path / "control.sock"
    async with ControlServer(Registry(session), path):
        reader, writer = await asyncio.open_unix_connection(str(path))
        writer.write(json.dumps({"command": "stop", "transaction": 9, "timing": "now"}).encode() + b"\n")
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
        {"command": "start", "charger": "charger-a", "id_tag": "REMOTE", "timing": "now"},
        {"command": "reset", "charger": "charger-a", "timing": "now"},
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
async def test_after_waits_before_checking_active_transaction(monkeypatch):
    session = Session()
    registry = Registry(session)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        registry.active["charger-a"] = [17]

    monkeypatch.setattr("ocpp_csms.control.asyncio.sleep", fake_sleep)

    response = await dispatch_control(
        registry,
        {"command": "reset", "charger": "charger-a", "timing": "after", "seconds": 5},
    )

    assert sleeps == [5]
    assert response["error"] == "active_transaction"
    assert session.calls == []


@pytest.mark.asyncio
async def test_within_waits_for_active_transaction_to_finish(monkeypatch):
    session = Session()
    registry = Registry(session, active={"charger-a": [17]})
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            registry.active["charger-a"] = []

    monkeypatch.setattr("ocpp_csms.control.asyncio.sleep", fake_sleep)

    response = await dispatch_control(
        registry,
        {"command": "reset", "charger": "charger-a", "timing": "within", "seconds": 5},
    )

    assert response["ok"] is True
    assert sleeps == [1, 1]
    assert session.calls == [("reset", "Soft")]


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
async def test_rfid_version_queries_charger():
    session = Session()

    response = await dispatch_control(
        Registry(session),
        {"command": "rfid_version"},
    )

    assert response == {
        "ok": True,
        "response": {"list_version": 3, "charger": "charger-a"},
    }
    assert session.calls == [("rfid_version",)]


@pytest.mark.asyncio
async def test_rfid_export_sends_next_full_list_and_records_only_after_acceptance():
    session = Session()
    registry = Registry(session)

    response = await dispatch_control(
        registry,
        {
            "command": "rfid_export",
            "entries": [
                {"rfid": "CARD-A", "name": "Alice", "enabled": True},
                {"rfid": "CARD-B", "name": None, "enabled": True},
            ],
            "source_file": "rfid.csv",
            "list_hash": "hash",
        },
    )

    assert response["response"]["status"] == "Accepted"
    assert response["response"]["previous_version"] == 3
    assert response["response"]["list_version"] == 4
    assert response["response"]["verified_version"] == 3
    assert session.calls == [
        ("rfid_version",),
        (
            "rfid_send",
            4,
            [
                {"rfid": "CARD-A", "name": "Alice", "enabled": True},
                {"rfid": "CARD-B", "name": None, "enabled": True},
            ],
        ),
        ("rfid_version",),
    ]
    assert registry.rfid_lists[0]["list_version"] == 4
    assert registry.rfid_lists[0]["verified_version"] == 3


@pytest.mark.asyncio
async def test_rejected_rfid_export_is_not_stored():
    class RejectingSession(Session):
        async def send_local_list(self, list_version, entries):
            self.calls.append(("rfid_send", list_version, entries))
            return SimpleNamespace(status="Failed")

    session = RejectingSession()
    registry = Registry(session)

    response = await dispatch_control(
        registry,
        {
            "command": "rfid_export",
            "entries": [{"rfid": "CARD-A", "name": None, "enabled": True}],
            "source_file": "rfid.csv",
            "list_hash": "hash",
        },
    )

    assert response["response"]["status"] == "Failed"
    assert registry.rfid_lists == []
    assert session.calls == [
        ("rfid_version",),
        ("rfid_send", 4, [{"rfid": "CARD-A", "name": None, "enabled": True}]),
    ]


@pytest.mark.asyncio
async def test_rfid_export_version_uses_recorded_history_to_avoid_reuse():
    session = Session()
    registry = Registry(session)
    registry.rfid_lists.append(
        {
            "charger_id": "charger-a",
            "list_version": 8,
            "entries": [],
            "source_file": "rfid.csv",
            "list_hash": "old",
            "verified_version": 8,
        }
    )

    response = await dispatch_control(
        registry,
        {
            "command": "rfid_export",
            "entries": [],
            "source_file": "rfid.csv",
            "list_hash": "empty-hash",
        },
    )

    assert response["response"]["list_version"] == 9
    assert ("rfid_send", 9, []) in session.calls


@pytest.mark.asyncio
async def test_rfid_clear_sends_empty_full_list_version_zero_and_records_acceptance():
    class ClearSession(Session):
        async def get_local_list_version(self):
            self.calls.append(("rfid_version",))
            return SimpleNamespace(list_version=5 if len(self.calls) == 1 else 0)

    session = ClearSession()
    registry = Registry(session)

    response = await dispatch_control(registry, {"command": "rfid_clear"})

    assert response["response"]["status"] == "Accepted"
    assert response["response"]["list_version"] == 0
    assert response["response"]["verified_version"] == 0
    assert ("rfid_send", 0, []) in session.calls
    assert registry.rfid_lists == [
        {
            "charger_id": "charger-a",
            "list_version": 0,
            "entries": [],
            "source_file": None,
            "list_hash": "empty",
            "verified_version": 0,
        }
    ]
