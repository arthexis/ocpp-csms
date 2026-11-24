"""Build and validate the GWay v1 provider manifest without privileged execution."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys


def manifest(executable: str, revision: str, data_dir: str, mode: str) -> dict:
    path = Path(executable)
    data = Path(data_dir)
    if (not path.is_absolute() or str(path) != executable or ".." in path.parts
            or path.name != "ocpp-csms-mcp" or path.is_symlink()
            or not data.is_absolute() or str(data) != data_dir or ".." in data.parts
            or not re.fullmatch(r"[0-9a-f]{40}", revision)
            or mode not in ("read", "execute")):
        raise ValueError("invalid provider registration")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or not info.st_mode & 0o111:
        raise ValueError("unsafe provider executable")
    argv = [executable, "--data-dir", data_dir]
    if mode == "execute":
        argv.append("--enable-execute")
    result = {"source_id": "ocpp", "revision": revision, "executable": executable, "argv": argv}
    if len(json.dumps(result).encode()) > 8192:
        raise ValueError("oversized provider manifest")
    return result


def check_previous(previous: dict, current: Path, data_dir: str) -> None:
    if type(previous) is not dict or not current.is_symlink():
        raise ValueError("no previous registration")
    exe = str(current.resolve(strict=True) / "venv/bin/ocpp-csms-mcp")
    revision = previous.get("revision")
    if not isinstance(revision, str) or current.resolve().name not in (revision[:12], revision[:12] + "-dev"):
        raise ValueError("previous revision mismatch")
    mode = "execute" if previous.get("argv", [])[-1:] == ["--enable-execute"] else "read"
    if previous != manifest(exe, revision, data_dir, mode):
        raise ValueError("previous provider does not match writer")


def probe(data_dir: Path, mode: str) -> None:
    from ocpp_csms.mcp_provider import _evidence
    with _evidence(data_dir) as db:
        db.execute("SELECT 1").fetchone()
    if mode == "execute":
        path = data_dir / "control.sock"
        info = path.lstat()
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o660 or not os.access(path, os.W_OK)):
            raise ValueError("control socket does not match service identity")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    render = subs.add_parser("render")
    render.add_argument("--executable", required=True)
    render.add_argument("--revision", required=True)
    render.add_argument("--data-dir", required=True)
    render.add_argument("--mode", choices=("read", "execute"), required=True)
    prior = subs.add_parser("check-previous")
    prior.add_argument("--current", type=Path, required=True)
    prior.add_argument("--data-dir", required=True)
    verify = subs.add_parser("probe")
    verify.add_argument("--data-dir", type=Path, required=True)
    verify.add_argument("--mode", choices=("read", "execute"), required=True)
    args = parser.parse_args()
    try:
        if args.command == "render":
            print(json.dumps(manifest(args.executable, args.revision, args.data_dir, args.mode), sort_keys=True))
        elif args.command == "check-previous":
            raw = sys.stdin.read(8193)
            if len(raw) > 8192:
                raise ValueError("oversized provider manifest")
            check_previous(json.loads(raw), args.current, args.data_dir)
        else:
            probe(args.data_dir, args.mode)
    except (OSError, ValueError, RuntimeError, TypeError, AttributeError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
