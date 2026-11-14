"""Read-only live CSMS storage authority for schema handoff.

Never infer the active writer from prepared schema directories or release
symlinks. Used by the installer before any service stop or schema handoff.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

def _show(service: str, key: str) -> str:
    return subprocess.run(
        ["systemctl", "show", service, "-p", key, "--value"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

def _single_data_dir(argv: list[str]) -> Path:
    values = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--data-dir"]
    if len(values) != 1 or "serve" not in argv:
        raise RuntimeError("active_storage: CSMS writer argv is ambiguous")
    return Path(values[0]).expanduser().resolve(strict=True)

def active_storage(
    service: str = "ocpp-csms.service",
    *,
    storage_root: str | Path,
    proc_root: Path = Path("/proc"),
) -> Path | None:
    if not re.fullmatch(r"[a-zA-Z0-9_.@-]+\.service", service):
        raise RuntimeError("active_storage: invalid service name")
    load = _show(service, "LoadState")
    if load == "not-found":
        # A completely absent unit is the only supported first-install case.
        return None
    if load != "loaded":
        raise RuntimeError(f"active_storage: CSMS LoadState is {load!r}")
    state = _show(service, "ActiveState")
    if state != "active":
        raise RuntimeError(f"active_storage: existing CSMS unit is {state!r}")
    pid_text = _show(service, "MainPID")
    try:
        pid = int(pid_text)
    except ValueError as exc:
        raise RuntimeError("active_storage: invalid MainPID") from exc
    if pid <= 0:
        raise RuntimeError("active_storage: CSMS writer has no MainPID")
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
        argv = [part.decode("utf-8") for part in raw.split(b"\0") if part]
        live = _single_data_dir(argv)
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("active_storage: cannot inspect CSMS MainPID") from exc

    # systemctl's ExecStart show format is systemd-structured, not a
    # shell command. Match its one --data-dir argument without executing it.
    unit = _show(service, "ExecStart")
    candidates = re.findall(r"--data-dir[ =]([^ ;}\n]+)", unit)
    if len(candidates) != 1 or "serve" not in unit:
        raise RuntimeError("active_storage: installed CSMS unit is ambiguous")
    expected = Path(candidates[0]).expanduser().resolve(strict=True)
    if live != expected:
        raise RuntimeError("active_storage: unit and writer storage disagree")
    root = Path(storage_root).expanduser().resolve(strict=True)
    if live.parent != root / "schemas":
        raise RuntimeError("active_storage: active schema outside storage root")
    if not live.name.isdecimal() or int(live.name) < 1:
        raise RuntimeError("active_storage: invalid active schema directory")
    return live

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only active CSMS storage probe")
    parser.add_argument("--service", default="ocpp-csms.service")
    parser.add_argument("--storage-root", required=True)
    args = parser.parse_args(argv)
    try:
        result = active_storage(args.service, storage_root=args.storage_root)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps({"active": str(result) if result else None}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
