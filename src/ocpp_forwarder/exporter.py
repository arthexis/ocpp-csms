from __future__ import annotations

import json
import subprocess
from typing import Any

EXPORT_SCHEMA = "ocpp-csms/export/v1"


class ExportError(RuntimeError):
    pass


def read_export(
    command: str,
    *,
    data_dir: str,
    after: int,
    limit: int,
) -> dict[str, Any]:
    result = subprocess.run(
        [
            command,
            "--data-dir",
            data_dir,
            "export",
            "--after",
            str(after),
            "--limit",
            str(limit),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ExportError(f"OCPP CSMS export failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ExportError("OCPP CSMS export returned invalid JSON") from exc
    if payload.get("schema") != EXPORT_SCHEMA or not isinstance(payload.get("data"), dict):
        raise ExportError("OCPP CSMS export returned an unsupported contract")
    return payload["data"]
