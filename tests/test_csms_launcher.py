import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "csms.sh"


def _write_fake_command(path: Path, *, exit_code: int = 0) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\"\nexit " + str(exit_code) + "\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


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
