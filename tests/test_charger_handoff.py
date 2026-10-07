from types import SimpleNamespace

import pytest

from ocpp_csms.control import dispatch_control


class Session:
    def __init__(self):
        self.calls = []

    async def reset(self, reset_type="Soft"):
        self.calls.append(("reboot", reset_type))
        return SimpleNamespace(status="Accepted")


class Registry:
    def __init__(self):
        self.sessions = {}
        self.historical_chargers = set()

    def session(self, charge_point_id):
        return self.sessions.get(charge_point_id)

    def connected_chargers(self):
        return sorted(self.sessions)

    def active_transaction_ids(self, charge_point_id):
        return []

    def record_control_event(self, event, *, charger_id, details=None):
        pass


@pytest.mark.asyncio
async def test_inference_follows_live_charger_after_hardware_handoff():
    registry = Registry()
    charger_a = Session()
    charger_b = Session()

    # The first physical charger is connected, then removed. Its identity can
    # remain in historical evidence without remaining eligible for live control.
    registry.sessions["charger-a"] = charger_a
    first = await dispatch_control(registry, {"command": "reboot", "timing": "now"})
    registry.historical_chargers.add("charger-a")
    del registry.sessions["charger-a"]

    # A different physical charger connects to the same CSMS under its own
    # charge-point ID. Omitted selection must now resolve to this live charger.
    registry.sessions["charger-b"] = charger_b
    second = await dispatch_control(registry, {"command": "reboot", "timing": "now"})

    assert first["ok"] is True
    assert second["ok"] is True
    assert charger_a.calls == [("reboot", "Soft")]
    assert charger_b.calls == [("reboot", "Soft")]
    assert registry.historical_chargers == {"charger-a"}
    assert registry.connected_chargers() == ["charger-b"]
