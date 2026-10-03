from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Protocol


class SystemProbe(Protocol):
    def executable_exists(self, command: str) -> bool: ...
    def service_exists(self, service: str) -> bool: ...
    def directory_ready(self, path: str) -> bool: ...


class LocalSystemProbe:
    """Read-only host checks used by field preflight.

    Service mutation is intentionally absent from chunk 1.
    """

    def executable_exists(self, command: str) -> bool:
        candidate = Path(command).expanduser()
        if candidate.is_absolute() or "/" in command:
            return candidate.is_file() and os.access(candidate, os.X_OK)
        return shutil.which(command) is not None

    def service_exists(self, service: str) -> bool:
        result = subprocess.run(
            ["systemctl", "show", service, "--property", "LoadState", "--value"],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() not in {"", "not-found"}

    def directory_ready(self, path: str) -> bool:
        candidate = Path(path).expanduser()
        if candidate.exists():
            return candidate.is_dir() and os.access(candidate, os.W_OK)
        parent = candidate.parent
        return parent.is_dir() and os.access(parent, os.W_OK)
