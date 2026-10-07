import asyncio
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ocpp_csms.control import ControlServer


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "csms.sh"


def _write_fake_command(path: Path, *, exit_code: int = 0) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\"\nexit " + str(exit_code) + "\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class _LauncherSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def reset(self, reset_type="Soft"):
        self.calls.append(("reboot", reset_type))
        return SimpleNamespace(status="Accepted")


class _LauncherRegistry:
    def __init__(self, session: _LauncherSession) -> None:
        self._session = session

    def session(self, charge_point_id):
        return self._session if charge_point_id == "charger-a" else None

    def connected_chargers(self):
        return ["charger-a"]

    def physical_connector_ids(self, charge_point_id):
        return []

    def active_transaction_ids(self, charge_point_id):
        return []

    def record_control_event(self, event, *, charger_id, details=None):
        return None


def test_launcher_is_executable():
    assert LAUNCHER.stat().st_mode & stat.S_IXUSR


def test_launcher_uses_explicit_venv_and_forwards_arguments(tmp_path):
    venv = tmp_path / "custom-venv"
    _write_fake_command(venv / "bin" / "ocpp-csms")

    result = subprocess.run(
        [str(LAUNCHER), "status", "charger-01", "--charging"],
        cwd=tmp_path,
        env={**os.environ, "OCPP_CSMS_VENV": str(venv)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["status", "charger-01", "--charging"]


@pytest.mark.asyncio
async def test_launcher_reaches_connected_charger_control_session(tmp_path):
    installed_command = Path(sys.prefix) / "bin" / "ocpp-csms"
    assert installed_command.is_file()

    session = _LauncherSession()
    async with ControlServer(_LauncherRegistry(session), tmp_path / "control.sock"):
        process = await asyncio.create_subprocess_exec(
            str(LAUNCHER),
            "--data-dir",
            str(tmp_path),
            "reboot",
            "charger-a",
            "--now",
            cwd=str(tmp_path),
            env={**os.environ, "OCPP_CSMS_VENV": sys.prefix},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

    assert process.returncode == 0, stderr.decode()
    assert stdout.decode().strip() == "Accepted"
    assert session.calls == [("reboot", "Soft")]


def test_launcher_preserves_underlying_exit_code(tmp_path):
    venv = tmp_path / "custom-venv"
    _write_fake_command(venv / "bin" / "ocpp-csms", exit_code=7)

    result = subprocess.run(
        [str(LAUNCHER), "status"],
        cwd=tmp_path,
        env={**os.environ, "OCPP_CSMS_VENV": str(venv)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 7


def test_launcher_reports_missing_environment(tmp_path):
    result = subprocess.run(
        [str(LAUNCHER), "status"],
        cwd=tmp_path,
        env={**os.environ, "OCPP_CSMS_VENV": str(tmp_path / "missing")},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "CSMS virtual environment not found." in result.stderr
