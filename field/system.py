from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Protocol


class SystemProbe(Protocol):
    def executable_exists(self, command: str) -> bool: ...
    def service_exists(self, service: str) -> bool: ...
    def service_active(self, service: str) -> bool: ...
    def service_start(self, service: str) -> bool: ...
    def service_stop(self, service: str) -> bool: ...
    def directory_ready(self, path: str) -> bool: ...
    def port_listening(self, host: str, port: int) -> bool: ...
    def socket_exists(self, path: str) -> bool: ...


class LocalSystemProbe:
    """Host checks and narrowly scoped service operations for field runs."""

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

    def service_active(self, service: str) -> bool:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service],
            check=False,
        )
        return result.returncode == 0

    def service_start(self, service: str) -> bool:
        return subprocess.run(["systemctl", "start", service], check=False).returncode == 0

    def service_stop(self, service: str) -> bool:
        return subprocess.run(["systemctl", "stop", service], check=False).returncode == 0

    def directory_ready(self, path: str) -> bool:
        candidate = Path(path).expanduser()
        if candidate.exists():
            return candidate.is_dir() and os.access(candidate, os.W_OK)
        parent = candidate.parent
        return parent.is_dir() and os.access(parent, os.W_OK)

    def port_listening(self, host: str, port: int) -> bool:
        targets = [host]
        if host in {"0.0.0.0", "::"}:
            targets = ["127.0.0.1", "::1"]
        for target in targets:
            family = socket.AF_INET6 if ":" in target else socket.AF_INET
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.2)
                try:
                    if sock.connect_ex((target, port)) == 0:
                        return True
                except OSError:
                    continue
        return False

    def socket_exists(self, path: str) -> bool:
        return Path(path).expanduser().is_socket()
