"""CI-only storage-contract integration on a disposable Ansible runner.

Use the real installed CSMS writer, render Discover's Ansible unit without
installing it, and execute only its read-only ExecStartPre. No network changes.
"""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess

import yaml
from jinja2 import Environment, StrictUndefined

REPO = Path(__file__).resolve().parents[2]
ROOT = Path.home() / "ocpp-csms-data"


def active_writer() -> Path:
    result = subprocess.run(
        ["systemctl", "show", "ocpp-csms.service", "-p", "MainPID", "--value"],
        capture_output=True, text=True, check=True,
    )
    pid = int(result.stdout.strip())
    if pid <= 0:
        raise AssertionError("no active CSMS MainPID")
    argv = [
        arg.decode() for arg in (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
        if arg
    ]
    found = [argv[i + 1] for i, token in enumerate(argv[:-1]) if token == "--data-dir"]
    if len(found) != 1 or "serve" not in argv:
        raise AssertionError("ambiguous active CSMS writer data-dir")
    return Path(found[0]).resolve(strict=True)


def render_unit(selected: Path) -> str:
    defaults = yaml.safe_load((REPO / "ansible/roles/ocpp_discover/defaults/main.yml").read_text())
    if defaults["ocpp_discover_data_dir"].strip() != "{{ ocpp_csms_service_data_dir | mandatory }}":
        raise AssertionError("Discover default is no longer selected by CSMS")
    jinja = Environment(undefined=StrictUndefined)
    jinja.filters["mandatory"] = lambda value: value
    ctx = {"ansible_env": {"HOME": str(Path.home())},
           "ocpp_csms_service_data_dir": str(selected)}
    for name, value in defaults.items():
        ctx[name] = jinja.from_string(value).render(ctx) if isinstance(value, str) else value
    # A separate CI-only Ansible playbook targets an unaddressed dummy, never
    # GitHub's existing physical-facing eth0. No override is available in
    # ordinary deployment or on self-hosted runners.
    ci_interface = os.environ.get("OCPP_STAGE_A_TEST_INTERFACE")
    if ci_interface is not None:
        if not (os.environ.get("GITHUB_ACTIONS") == "true"
                and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
                and ci_interface == "ocppci0"):
            raise AssertionError("unsafe CI interface override")
        ctx["ocpp_discover_interface"] = ci_interface
    template = (REPO / "ansible/roles/ocpp_discover/templates/ocpp-discover.service.j2").read_text()
    return jinja.from_string(template).render(ctx)


def command(unit: str, key: str) -> list[str]:
    rows = [line for line in unit.splitlines() if line.startswith(key + "=")]
    if len(rows) != 1:
        raise AssertionError(f"expected exactly one {key}")
    return shlex.split(rows[0].split("=", 1)[1])


def command_data_dir(argv: list[str]) -> Path:
    found = [argv[i + 1] for i, token in enumerate(argv[:-1]) if token == "--data-dir"]
    if len(found) != 1:
        raise AssertionError("Discover unit data-dir missing/ambiguous")
    return Path(found[0]).resolve(strict=True)


def persistent_snapshot():
    root = Path("/var/lib/ocpp-discover")
    if not root.exists():
        return None
    return sorted(
        (str(path.relative_to(root)), path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
    )


def main() -> None:
    active = active_writer()
    expected_schema = os.environ.get("TEST_SCHEMA")
    if expected_schema and active != ROOT / "schemas" / expected_schema:
        raise AssertionError("active CSMS schema differs from selected candidate")
    if not (active / "ocpp-csms.sqlite3").is_file():
        raise AssertionError("missing active CSMS database")

    rendered = render_unit(active)
    preflight = command(rendered, "ExecStartPre")
    startup = command(rendered, "ExecStart")
    if command_data_dir(preflight) != active or command_data_dir(startup) != active:
        raise AssertionError("Discover unit diverges from live CSMS data-dir")
    if preflight[1:3] != ["-m", "ocpp_discover.storage_contract"]:
        raise AssertionError("Discover does not use read-only ExecStartPre")
    if startup[1:3] != ["-m", "ocpp_discover"]:
        raise AssertionError("Discover start command changed")

    before = persistent_snapshot()
    positive = subprocess.run(preflight, capture_output=True, text=True)
    if positive.returncode:
        raise AssertionError(f"CSMS live-storage preflight failed: {positive.stderr}")
    negative = preflight.copy()
    negative[-1] = str(ROOT)
    rejection = subprocess.run(negative, capture_output=True, text=True)
    if rejection.returncode == 0 or "differs" not in rejection.stderr:
        raise AssertionError("old unversioned parent was not rejected")
    if persistent_snapshot() != before:
        raise AssertionError("read-only preflight changed Discover persistent state")
    if (ROOT / "ocpp-csms.sqlite3").exists():
        raise AssertionError("unexpected unversioned fallback database")

    if Path("/sys/class/net/eth0").exists():
        installed = Path("/etc/systemd/system/ocpp-discover.service")
        if not installed.is_file() or installed.read_text() != rendered:
            raise AssertionError("installed Discover unit differs from selected schema")
        subprocess.run(["systemctl", "is-active", "--quiet", "ocpp-discover.service"], check=True)
        print(f"PASS: installed Discover and CSMS use {active}")
    else:
        print(
            f"PASS: live CSMS, rendered Discover and ExecStartPre use {active}; "
            "eth0 absent, Discover service install/restart NOT TESTED"
        )


if __name__ == "__main__":
    main()
