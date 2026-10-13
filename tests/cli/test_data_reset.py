"""Safety contracts for explicitly requested destructive test-data reset."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ocpp_csms.cli.data import reset_test_data


def permitted():
    return SimpleNamespace(allowed=True, connected_chargers=(), active_chargers=(), reason=None)


def test_default_dry_run_is_read_only(tmp_path):
    data = tmp_path / "ocpp-csms-data"
    data.mkdir()
    (data / "marker").write_text("test")
    with patch("ocpp_csms.cli.data.process_is_running", return_value=False), \
         patch("ocpp_csms.cli.data.subprocess.run", return_value=SimpleNamespace(returncode=3)), \
         patch("ocpp_csms.cli.data.evaluate_preflight", return_value=permitted()):
        result = reset_test_data(data, test_only=True)
    assert result["decision"] == "dry-run"
    assert (data / "marker").read_text() == "test"
    assert not (tmp_path / "ocpp-csms-data-test-backups").exists()


def test_reset_moves_data_and_reinitializes(tmp_path):
    data = tmp_path / "ocpp-csms-data"
    data.mkdir()
    (data / "marker").write_text("secret")
    with patch("ocpp_csms.cli.data.process_is_running", return_value=False), \
         patch("ocpp_csms.cli.data.subprocess.run", return_value=SimpleNamespace(returncode=3)), \
         patch("ocpp_csms.cli.data.evaluate_preflight", return_value=permitted()), \
         patch("ocpp_csms.cli.data.initialize_storage") as init:
        result = reset_test_data(data, test_only=True, now=True)
    assert result["decision"] == "reset"
    assert (Path(result["backup"]) / "marker").read_text() == "secret"
    init.assert_called_once_with(str(data))


@pytest.mark.parametrize("reason, running, active", [
    ("running process", True, False), ("active service", False, True),
])
def test_refuses_live_service(tmp_path, reason, running, active):
    data = tmp_path / "data"
    data.mkdir()
    with patch("ocpp_csms.cli.data.process_is_running", return_value=running), \
         patch("ocpp_csms.cli.data.subprocess.run", return_value=SimpleNamespace(returncode=0 if active else 3)):
        with pytest.raises(ValueError):
            reset_test_data(data, test_only=True, now=True)
    assert data.exists()


def test_refuses_without_test_only(tmp_path):
    with pytest.raises(ValueError, match="test-only"):
        reset_test_data(tmp_path, test_only=False, now=True)


def test_refuses_active_charge_evidence(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    unsafe = SimpleNamespace(allowed=False, connected_chargers=(), active_chargers=("CP",), reason="active charging")
    with patch("ocpp_csms.cli.data.process_is_running", return_value=False), \
         patch("ocpp_csms.cli.data.subprocess.run", return_value=SimpleNamespace(returncode=3)), \
         patch("ocpp_csms.cli.data.evaluate_preflight", return_value=unsafe):
        with pytest.raises(ValueError, match="active charging"):
            reset_test_data(data, test_only=True, now=True)
    assert data.exists()


def test_refuses_symlinked_data(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    link = tmp_path / "link"
    link.symlink_to(data)
    with pytest.raises(ValueError, match="symlinked"):
        reset_test_data(link, test_only=True, now=True)
