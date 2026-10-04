from types import SimpleNamespace

import pytest

import ocpp_csms.app as app
from ocpp_csms.app import build_parser, run_profile
from ocpp_csms.control import dispatch_control


class SmartChargingSession:
    def __init__(self):
        self.profile = None

    async def set_charging_profile(self, connector_id, profile):
        self.profile = (connector_id, profile)
        return SimpleNamespace(status="Accepted")

    async def clear_charging_profile(
        self,
        *,
        profile_id=None,
        connector_id=None,
        purpose=None,
        stack_level=None,
    ):
        self.profile = None
        return SimpleNamespace(status="Accepted")

    async def get_composite_schedule(self, connector_id, duration, charging_rate_unit=None):
        if self.profile is None:
            return SimpleNamespace(status="Rejected")
        _, profile = self.profile
        schedule = profile["chargingSchedule"]
        return SimpleNamespace(
            status="Accepted",
            connector_id=connector_id,
            schedule_start="2026-10-03T20:00:00Z",
            charging_schedule=schedule,
        )


class Registry:
    def __init__(self, session):
        self._session = session

    def session(self, charge_point_id):
        return self._session if charge_point_id == "charger-a" else None

    def connected_chargers(self):
        return ["charger-a"]

    def active_transaction_ids(self, charge_point_id):
        return []

    def record_control_event(self, event, *, charger_id, details=None):
        raise AssertionError("profile workflow should not use configuration guards")


@pytest.mark.parametrize("watts", [60000, 45000])
def test_profile_set_composite_clear_workflow(monkeypatch, capsys, watts):
    session = SmartChargingSession()
    registry = Registry(session)

    async def in_process_send_control(data_dir, request):
        return await dispatch_control(registry, request)

    monkeypatch.setattr(app, "send_control", in_process_send_control)
    parser, _ = build_parser()

    set_args = parser.parse_args(["profile", "set", "max-power", "--watts", str(watts)])
    assert run_profile(set_args) == 0
    assert capsys.readouterr().out.strip() == "Accepted"

    connector_id, profile = session.profile
    assert connector_id == 0
    assert profile["chargingProfilePurpose"] == "ChargePointMaxProfile"
    assert profile["chargingSchedule"]["chargingRateUnit"] == "W"
    assert profile["chargingSchedule"]["chargingSchedulePeriod"] == [
        {"startPeriod": 0, "limit": watts}
    ]

    composite_args = parser.parse_args(["profile", "composite"])
    assert run_profile(composite_args) == 0
    output = capsys.readouterr().out
    assert "Rate unit: W" in output
    assert f"{watts} W" in output

    clear_args = parser.parse_args(["profile", "clear"])
    assert run_profile(clear_args) == 0
    assert capsys.readouterr().out.strip() == "Accepted"
    assert session.profile is None

    assert run_profile(composite_args) == 1
    assert capsys.readouterr().out.strip() == "Rejected"
