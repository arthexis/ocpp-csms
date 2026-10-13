"""Canonical reservation CLI and read-only detail dispatch."""
import sys
import pytest
from ocpp_csms.cli import build_parser, main


def test_reserve_commands_registered():
    parser, commands = build_parser()
    assert "reserve" in commands
    assert "reservation" not in commands
    assert parser.parse_args(["reserve"]).reservation_action is None
    assert parser.parse_args(["reserve", "create", "-c", "1", "--rfid", "ABC", "--id", "42", "--until", "1h"]).reservation_action == "create"
    assert parser.parse_args(["reserve", "cancel", "42"]).reservation_action == "cancel"


@pytest.mark.parametrize("argv", [["reservation", "list"], ["reservation", "cancel", "42"], ["reserve", "list"]])
def test_removed_spellings_rejected(argv):
    parser, _ = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(argv)
    assert exc.value.code == 2


def test_reserve_detail_is_read_only(monkeypatch, tmp_path, capsys):
    seen = []
    def fake_list(data_dir, **kwargs):
        seen.append(kwargs)
        return [{"id": 42, "cp": "CP1", "connector": 1, "rfid": "ABC",
                 "requested": "2026-10-01", "expires": "2026-10-02", "status": "Accepted"},
                {"id": 43, "cp": "CP1", "connector": 2, "rfid": "XYZ",
                 "requested": "2026-10-01", "expires": "2026-10-02", "status": "Canceled"}]
    monkeypatch.setattr("ocpp_csms.cli.reservation.list_reservations", fake_list)
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "--data-dir", str(tmp_path), "reserve", "42", "-j"])
    assert main() == 0
    assert seen[0]["include_all"] is True
    assert '"id": 42' in capsys.readouterr().out


def test_reserve_list_defaults_to_current_evidence(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr("ocpp_csms.cli.reservation.list_reservations", lambda data_dir, **kwargs: seen.append(kwargs) or [])
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "--data-dir", str(tmp_path), "reserve"])
    assert main() == 0
    assert seen[0]["include_all"] is False


@pytest.mark.parametrize("reservation_id", ["0", "-1"])
def test_reserve_detail_rejects_nonpositive_ids(monkeypatch, tmp_path, reservation_id):
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "--data-dir", str(tmp_path),
                                      "reserve", reservation_id])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_reserve_detail_filters_history_and_never_sends_control(monkeypatch, tmp_path, capsys):
    import json
    def forbidden(*args, **kwargs):
        raise AssertionError("read-only reservation detail attempted remote control")
    def fake_list(data_dir, **kwargs):
        assert kwargs == {"charger": "CP1", "include_all": True, "since": None}
        return [
            {"id": 42, "cp": "CP1", "connector": 1, "rfid": "A",
             "requested": "2026-10-01", "expires": None, "status": "Canceled"},
            {"id": 43, "cp": "CP1", "connector": 2, "rfid": "B",
             "requested": "2026-10-01", "expires": None, "status": "Accepted"},
        ]
    monkeypatch.setattr("ocpp_csms.cli.reservation.send_control", forbidden)
    monkeypatch.setattr("ocpp_csms.cli.reservation.list_reservations", fake_list)
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "--data-dir", str(tmp_path),
                                      "reserve", "42", "--cp", "CP1", "-j"])
    assert main() == 0
    assert [row["id"] for row in json.loads(capsys.readouterr().out)] == [42]


def test_reserve_missing_detail_has_empty_json(monkeypatch, tmp_path, capsys):
    import json
    monkeypatch.setattr("ocpp_csms.cli.reservation.list_reservations",
                        lambda *args, **kwargs: [])
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "--data-dir", str(tmp_path),
                                      "reserve", "99", "-j"])
    assert main() == 0
    assert json.loads(capsys.readouterr().out) == []


@pytest.mark.parametrize("argv", [
    ["reserve", "create", "--help"],
    ["reserve", "cancel", "--help"],
    ["reserve", "--help"],
])
def test_reserve_help(monkeypatch, argv, capsys):
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", *argv])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out
