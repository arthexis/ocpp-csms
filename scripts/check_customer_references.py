"""Reject known customer and field identifiers from tracked repository files.

Keep this small gate limited to identifiers confirmed by the audit.
Manufacturer/protocol references (for example ABB) are intentionally allowed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def forbidden_patterns() -> tuple[re.Pattern[str], ...]:
    # Split literals so that the checker does not report itself as a match.
    names = ("au" + "di", "por" + "sche")
    real_charger_id = "T175" + "-IT1-0620-055"
    field_node_id = "gway" + "-005"
    return (
        re.compile(r"\\b(?:" + "|".join(names) + r")\\b", re.IGNORECASE),
        re.compile(re.escape(real_charger_id), re.IGNORECASE),
        re.compile(r"\\b" + re.escape(field_node_id) + r"\\b", re.IGNORECASE),
    )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root
    ).split(b"\\0")
    patterns = forbidden_patterns()
    violations: list[str] = []

    for raw_path in tracked:
        if not raw_path:
            continue
        path = Path(raw_path.decode("utf-8", errors="surrogateescape"))
        data = (root / path).read_bytes()
        if b"\\0" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in patterns):
                violations.append(f"{path}:{number}")

    if violations:
        print("Customer-specific reference found in tracked files:")
        for location in violations:
            print(f"  {location}")
        print("Use synthetic identifiers; keep customer-specific values in deployment configuration.")
        return 1

    print("Customer-reference audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
