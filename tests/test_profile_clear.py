import pytest

import ocpp_csms.app as app
from ocpp_csms.app import build_parser, run_profile


def test_profile_clear_without_filters_requests_clear_all(monkeypatch, capsys):
    sent = []

    async def fake_send_control(data_dir, request):
        sent.append(request)
        return {"ok": True, "response": {"status": "Accepted"}}

    monkeypatch.setattr(app, "send_control", fake_send_control)
    parser, _ = build_parser()
    args = parser.parse_args(["profile", "clear"])

    assert run_profile(args) == 0
    assert sent == [{"command": "clear_charging_profile"}]
    assert capsys.readouterr().out.strip() == "Accepted"


def test_profile_clear_maps_protocol_filters(monkeypatch):
    sent = []

    async def fake_send_control(data_dir, request):
        sent.append(request)
        return {"ok": True, "response": {"status": "Accepted"}}

    monkeypatch.setattr(app, "send_control", fake_send_control)
    parser, _ = build_parser()
    args = parser.parse_args([
        "profile",
        "clear",
        "--charger",
        "charger-a",
        "--id",
        "7",
        "--connector",
        "1",
        "--purpose",
        "ChargePointMaxProfile",
        "--stack-level",
        "2",
    ])

    assert run_profile(args) == 0
    assert sent == [{
        "command": "clear_charging_profile",
        "charger": "charger-a",
        "id": 7,
        "connector": 1,
        "purpose": "ChargePointMaxProfile",
        "stack_level": 2,
    }]


@pytest.mark.parametrize(
    "argv",
    [
        ["profile", "clear", "--id", "-1"],
        ["profile", "clear", "--connector", "-1"],
        ["profile", "clear", "--stack-level", "-1"],
    ],
)
def test_profile_clear_rejects_negative_filters_without_contacting_control(monkeypatch, capsys, argv):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("control should not be contacted")

    monkeypatch.setattr(app, "send_control", fail_if_called)
    parser, _ = build_parser()
    args = parser.parse_args(argv)

    assert run_profile(args) == 1
    assert "error:" in capsys.readouterr().out


def test_profile_clear_propagates_rejected_status(monkeypatch, capsys):
    async def fake_send_control(data_dir, request):
        return {"ok": True, "response": {"status": "Rejected"}}

    monkeypatch.setattr(app, "send_control", fake_send_control)
    parser, _ = build_parser()
    args = parser.parse_args(["profile", "clear", "--purpose", "TxProfile"])

    assert run_profile(args) == 1
    assert capsys.readouterr().out.strip() == "Rejected"
