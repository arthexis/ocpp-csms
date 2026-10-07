from types import SimpleNamespace

import pytest

from ocpp_csms.control import dispatch_control
from ocpp_csms.events import EventStore
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions import TransactionArchive


class ControlSession:
    def __init__(self):
        self.calls = []

    async def set_charging_profile(self, connector_id, profile):
        self.calls.append(("set", connector_id, profile))
        return SimpleNamespace(status="Accepted")

    async def clear_charging_profile(
        self,
        *,
        profile_id=None,
        connector_id=None,
        purpose=None,
        stack_level=None,
    ):
        self.calls.append(("clear", profile_id, connector_id, purpose, stack_level))
        return SimpleNamespace(status="Accepted")

    async def get_composite_schedule(self, connector_id, duration, charging_rate_unit=None):
        self.calls.append(("composite", connector_id, duration, charging_rate_unit))
        return SimpleNamespace(
            status="Accepted",
            connector_id=connector_id,
            schedule_start="2026-10-03T19:00:00Z",
            charging_schedule={
                "chargingRateUnit": charging_rate_unit or "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 60000}],
            },
        )


class Registry:
    def __init__(self, sessions=None):
        self.sessions = sessions or {}

    def session(self, charge_point_id):
        return self.sessions.get(charge_point_id)

    def connected_chargers(self):
        return sorted(self.sessions)

    def active_transaction_ids(self, charge_point_id):
        return []

    def record_control_event(self, event, *, charger_id, details=None):
        raise AssertionError("smart charging transport should not use configuration guards")


def charge_point_session:
    return ChargePointSession(
        "charger-a",
        SimpleNamespace(last_frame="test"),
        TransactionArchive(tmp_path),
        EventStore(tmp_path),
    )


def evidence(session, action):
    with session.events._connect() as connection:
        return connection.execute(
            "SELECT direction, payload_json FROM events WHERE action = ? ORDER BY id",
            (action,),
        ).fetchall()


@pytest.mark.asyncio
async def test_control_dispatches_smart_charging_commands_with_single_charger_inference():
    session = ControlSession()
    registry = Registry({"charger-a": session})
    profile = {"chargingProfileId": 1, "stackLevel": 0}

    set_response = await dispatch_control(
        registry,
        {"command": "set_charging_profile", "connector": 0, "profile": profile},
    )
    clear_response = await dispatch_control(
        registry,
        {
            "command": "clear_charging_profile",
            "connector": 0,
            "purpose": "ChargePointMaxProfile",
            "stack_level": 0,
        },
    )
    composite_response = await dispatch_control(
        registry,
        {
            "command": "get_composite_schedule",
            "connector": 0,
            "duration": 3600,
            "charging_rate_unit": "W",
        },
    )

    assert set_response["response"]["status"] == "Accepted"
    assert clear_response["response"]["status"] == "Accepted"
    assert composite_response["response"]["status"] == "Accepted"
    assert session.calls == [
        ("set", 0, profile),
        ("clear", None, 0, "ChargePointMaxProfile", 0),
        ("composite", 0, 3600, "W"),
    ]


@pytest.mark.asyncio
async def test_clear_all_profiles_passes_empty_filter_set():
    session = ControlSession()
    response = await dispatch_control(
        Registry({"charger-a": session}),
        {"command": "clear_charging_profile"},
    )

    assert response["response"]["status"] == "Accepted"
    assert session.calls == [("clear", None, None, None, None)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control_request", "error"),
    [
        ({"command": "set_charging_profile", "connector": -1, "profile": {}}, "invalid_connector"),
        ({"command": "set_charging_profile", "connector": 0, "profile": []}, "invalid_profile"),
        ({"command": "clear_charging_profile", "id": -1}, "invalid_profile_id"),
        ({"command": "clear_charging_profile", "connector": -1}, "invalid_connector"),
        ({"command": "clear_charging_profile", "purpose": ""}, "invalid_purpose"),
        ({"command": "clear_charging_profile", "stack_level": -1}, "invalid_stack_level"),
        ({"command": "get_composite_schedule", "connector": 0, "duration": 0}, "invalid_duration"),
        (
            {"command": "get_composite_schedule", "connector": 0, "duration": 60, "charging_rate_unit": "kW"},
            "invalid_charging_rate_unit",
        ),
    ],
)
async def test_control_rejects_invalid_smart_charging_arguments(control_request, error):
    response = await dispatch_control(Registry({"charger-a": ControlSession()}), control_request)
    assert response["error"] == error


@pytest.mark.asyncio
async def test_smart_charging_commands_require_live_charger():
    assert await dispatch_control(
        Registry(),
        {"command": "get_composite_schedule", "connector": 0, "duration": 60},
    ) == {"error": "no_charger_connected"}

    response = await dispatch_control(
        Registry({"charger-a": ControlSession(), "charger-b": ControlSession()}),
        {"command": "clear_charging_profile"},
    )
    assert response == {"error": "charger_required", "chargers": ["charger-a", "charger-b"]}


@pytest.mark.asyncio
async def test_set_charging_profile_builds_ocpp_request_and_records_evidence(charge_point_session):
    session = charge_point_session
    sent = []

    async def accepted(payload):
        sent.append(payload)
        return SimpleNamespace(status="Accepted")

    session.call = accepted
    profile = {
        "chargingProfileId": 1,
        "stackLevel": 0,
        "chargingProfilePurpose": "ChargePointMaxProfile",
        "chargingProfileKind": "Absolute",
        "chargingSchedule": {
            "chargingRateUnit": "W",
            "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 60000}],
        },
    }

    response = await session.set_charging_profile(0, profile)

    assert response.status == "Accepted"
    assert sent[0].connector_id == 0
    assert sent[0].cs_charging_profiles == profile
    rows = evidence(session, "SetChargingProfile")
    assert [row[0] for row in rows] == ["out", "in"]
    assert "60000" in rows[0][1]
    assert "Accepted" in rows[1][1]


@pytest.mark.asyncio
async def test_clear_charging_profile_builds_filters_and_records_evidence(charge_point_session):
    session = charge_point_session
    sent = []

    async def accepted(payload):
        sent.append(payload)
        return SimpleNamespace(status="Accepted")

    session.call = accepted

    response = await session.clear_charging_profile(
        connector_id=0,
        purpose="ChargePointMaxProfile",
        stack_level=0,
    )

    assert response.status == "Accepted"
    assert sent[0].id is None
    assert sent[0].connector_id == 0
    assert sent[0].charging_profile_purpose == "ChargePointMaxProfile"
    assert sent[0].stack_level == 0
    rows = evidence(session, "ClearChargingProfile")
    assert [row[0] for row in rows] == ["out", "in"]


@pytest.mark.asyncio
async def test_get_composite_schedule_preserves_semantic_response_and_evidence(charge_point_session):
    session = charge_point_session
    sent = []

    async def accepted(payload):
        sent.append(payload)
        return SimpleNamespace(
            status="Accepted",
            connector_id=0,
            schedule_start="2026-10-03T19:00:00Z",
            charging_schedule={
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 60000}],
            },
        )

    session.call = accepted

    response = await session.get_composite_schedule(0, 3600, "W")

    assert sent[0].connector_id == 0
    assert sent[0].duration == 3600
    assert sent[0].charging_rate_unit == "W"
    assert response.charging_schedule["chargingSchedulePeriod"][0]["limit"] == 60000
    rows = evidence(session, "GetCompositeSchedule")
    assert [row[0] for row in rows] == ["out", "in"]
    assert "3600" in rows[0][1]
    assert "60000" in rows[1][1]
