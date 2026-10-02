from __future__ import annotations

import os
from pathlib import Path

PID_FILENAME = "ocpp-csms.pid"


def write_pid(data_dir: str | Path) -> Path:
    path = Path(data_dir).expanduser() / PID_FILENAME
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    return path


def remove_pid(data_dir: str | Path) -> None:
    path = Path(data_dir).expanduser() / PID_FILENAME
    try:
        if int(path.read_text(encoding="utf-8").strip()) == os.getpid():
            path.unlink()
    except (FileNotFoundError, OSError, ValueError):
        pass


def process_is_running(data_dir: str | Path) -> bool:
    path = Path(data_dir).expanduser() / PID_FILENAME
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (FileNotFoundError, ProcessLookupError, OSError, ValueError):
        return False

    cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        command = cmdline.read_bytes().replace(b"\x00", b" ")
    except OSError:
        return True
    return b"ocpp-csms" in command or pid == os.getpid()
