"""A4b shared maintenance transport error and acknowledgment contracts."""
import pytest

from ocpp_csms.cli.maintenance_transport import MaintenanceControlError, maintenance_control


@pytest.mark.parametrize(("reply", "expected"), [
    ({"error": "rejected", "detail": "busy"}, "rejected: busy"),
    ({"error": "not_connected", "charger": "CP1"}, "not_connected: CP1"),
    ({"ok": True, "response": None}, "invalid OCPP response"),
    ({"ok": True, "response": []}, "invalid OCPP response"),
    (None, "invalid OCPP response"),
])
def test_rejected_or_malformed(monkeypatch, reply, expected):
    async def send(*_args):
        return reply
    monkeypatch.setattr("ocpp_csms.cli.maintenance_transport.send_control", send)
    with pytest.raises(MaintenanceControlError, match=expected):
        maintenance_control("/tmp", {"command": "trigger"})


@pytest.mark.parametrize("error", [ConnectionError("disconnected"), OSError("socket missing"), TimeoutError("timed out")])
def test_transport_failures(monkeypatch, error):
    async def send(*_args):
        raise error
    monkeypatch.setattr("ocpp_csms.cli.maintenance_transport.send_control", send)
    with pytest.raises(MaintenanceControlError, match=str(error)):
        maintenance_control("/tmp", {"command": "trigger"})


@pytest.mark.parametrize("payload", [{"status": "Accepted"}, {"status": "Scheduled"}, {"status": "Rejected"}, {}])
def test_valid_acknowledgments_are_not_reinterpreted(monkeypatch, payload):
    async def send(*_args):
        return {"ok": True, "response": payload}
    monkeypatch.setattr("ocpp_csms.cli.maintenance_transport.send_control", send)
    assert maintenance_control("/tmp", {"command": "trigger"}) == payload
