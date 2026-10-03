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
        self.calls.append(("reboot", reset_type))
        return SimpleNamespace(status="Accepted")

    async def get_configuration(self, keys=None):
        self.calls.append(("config", keys))
        return SimpleNamespace(
            configuration_key=[{"key": "HeartbeatInterval", "readonly": False, "value": "300"}],
            unknown_key=[],
        )


class Registry:
    def __init__(self, session=None, active=None):
        self.current = session
        self.active = active or {}

    def session(self, charge_point_id):
        return self.current if charge_point_id == "charger-a" else None

    def active_transaction_ids(self, charge_point_id):
        return list(self.active.get(charge_point_id, []))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control_request", "expected_call"),
    [
        (
            {"command": "start", "charger": "charger-a", "connector": 2, "id_tag": "REMOTE"},
            ("start", "REMOTE", 2),
        ),
        ({"command": "stop", "charger": "charger-a", "transaction": 42}, ("stop", 42)),
        ({"command": "reboot", "charger": "charger-a"}, ("reboot", "Soft")),
        ({"command": "reboot", "charger": "charger-a", "type": "Hard"}, ("reboot", "Hard")),
    ],
)
async def test_dispatches_supported_commands(control_request, expected_call):
    session = Session()

    response = await dispatch_control(Registry(session), control_request)

    assert response == {"ok": True, "response": {"status": "Accepted"}}
    assert session.calls == [expected_call]


@pytest.mark.asyncio
async def test_get_configuration_is_sent_when_idle():
    session = Session()

    response = await dispatch_control(
        Registry(session),
        {"command": "config", "charger": "charger-a", "keys": ["HeartbeatInterval"]},
    )

    assert response["ok"] is True
    assert session.calls == [("config", ["HeartbeatInterval"])]


@pytest.mark.asyncio
async def test_get_configuration_is_blocked_during_active_transaction():
    session = Session()

    response = await dispatch_control(
        Registry(session, active={"charger-a": [17]}),
        {"command": "config", "charger": "charger-a"},
    )

    assert response == {
        "error": "active_transaction",
        "charger": "charger-a",
        "transactions": [17],
    }
    assert session.calls == []


@pytest.mark.asyncio
async def test_forced_get_configuration_bypasses_active_transaction_guard():
    session = Session()

    response = await dispatch_control(
        Registry(session, active={"charger-a": [17]}),
        {"command": "config", "charger": "charger-a", "force": True},
    )

    assert response["ok"] is True
    assert session.calls == [("config", None)]


@pytest.mark.asyncio
async def test_other_charger_transaction_does_not_block_configuration():
    session = Session()

    response = await dispatch_control(
        Registry(session, active={"charger-b": [88]}),
        {"command": "config", "charger": "charger-a"},
    )

    assert response["ok"] is True
    assert session.calls == [("config", None)]


@pytest.mark.asyncio
async def test_disconnected_charger_is_not_queued():
    response = await dispatch_control(
        Registry(),
        {"command": "reboot", "charger": "charger-a"},
    )

    assert response == {"error": "charger_not_connected", "charger": "charger-a"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control_request", "error"),
    [
        ({"charger": "charger-a"}, "missing_command"),
        ({"command": "start"}, "missing_charger"),
        ({"command": "start", "charger": "charger-a"}, "missing_id_tag"),
        (
            {"command": "start", "charger": "charger-a", "id_tag": "REMOTE", "connector": -1},
            "invalid_connector",
        ),
        ({"command": "stop", "charger": "charger-a"}, "invalid_transaction"),
        ({"command": "reboot", "charger": "charger-a", "type": "Warm"}, "invalid_reset_type"),
        ({"command": "config", "charger": "charger-a", "keys": "HeartbeatInterval"}, "invalid_keys"),
        ({"command": "config", "charger": "charger-a", "force": "yes"}, "invalid_force"),
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

    response = await dispatch_control(
        Registry(FailingSession()),
        {"command": "reboot", "charger": "charger-a"},
    )

    assert response == {"error": "command_failed", "detail": "connection lost"}


@pytest.mark.asyncio
async def test_unix_socket_accepts_one_json_request(tmp_path):
    session = Session()
    path = tmp_path / "control.sock"

    async with ControlServer(Registry(session), path):
        reader, writer = await asyncio.open_unix_connection(str(path))
        writer.write(
            json.dumps({"command": "stop", "charger": "charger-a", "transaction": 9}).encode()
            + b"\n"
        )
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
