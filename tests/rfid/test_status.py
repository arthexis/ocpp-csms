from pathlib import Path

from ocpp_csms.status import appliance_status


def test_status_shows_allow_all_without_rfid_file(tmp_path: Path):
    data = appliance_status(tmp_path)

    assert data["rfid_authorization"] is None


def test_status_shows_rfid_file_and_count(tmp_path: Path):
    (tmp_path / "rfid.csv").write_text(
        "rfid,name\nCARD-A,Alice\nCARD-B,Bob\n",
        encoding="utf-8",
    )

    data = appliance_status(tmp_path)

    assert data["rfid_authorization"]["source"] == "rfid.csv"
    assert data["rfid_authorization"]["entries"] == 2
    assert data["rfid_authorization"]["valid"] is True


def test_status_marks_invalid_rfid_file(tmp_path: Path):
    (tmp_path / "rfid.csv").write_text(
        "rfid,enabled\nCARD-A,maybe\n",
        encoding="utf-8",
    )

    data = appliance_status(tmp_path)

    assert data["rfid_authorization"]["valid"] is False
