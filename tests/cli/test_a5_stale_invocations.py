"""Guard executable shell invocations against retired root CLI names.

Only scan deployment/operator script surfaces; historical docs and tests are
allowed to describe old names. This is not a general-purpose code search.
"""
from __future__ import annotations

from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
RETIRED = re.compile(
    r"(?<![\w-])ocpp-csms\s+(?:reservation|transactions|transaction|txns|diag|diagnostics|start|stop|recover)(?=\s|$|[;&|])"
)
SURFACES = ("scripts", "ansible", ".github/workflows")
SUFFIXES = {".sh", ".bash", ".yml", ".yaml"}


def stale_invocations(text: str) -> list[str]:
    return [m.group(0) for m in RETIRED.finditer(text)]


@pytest.mark.parametrize("command", [
    "ocpp-csms reservation list",
    "ocpp-csms transactions --json",
    "ocpp-csms transaction 42",
    "ocpp-csms txns",
    "ocpp-csms diag",
    "ocpp-csms diagnostics --now",
    "ocpp-csms start --cp CP1",
    "ocpp-csms stop",
    "ocpp-csms recover --dry-run",
])
def test_retired_executable_names_are_detected(command):
    assert stale_invocations(command)


@pytest.mark.parametrize("command", [
    "ocpp-csms txn -j",
    "ocpp-csms reserve 42",
    "ocpp-csms diagnose --now --location https://example.org/upload",
    "ocpp-discover diagnostics",
    "ocpp-csms export --cursor 123",
    "ocpp-csms report",
    "ocpp-csms mail send report",
    "ocpp-csms transactions.sqlite3",
])
def test_current_commands_and_noncommands_are_not_flagged(command):
    assert not stale_invocations(command)


def test_no_retired_shell_invocations_in_deployment_or_scripts():
    offenders = []
    for folder in SURFACES:
        base = ROOT / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in SUFFIXES:
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                # Ignore comments; only executable-style invocations are in scope.
                if line.lstrip().startswith("#"):
                    continue
                if stale_invocations(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")
    assert not offenders, "Retired CLI shell invocations:\n" + "\n".join(offenders)
