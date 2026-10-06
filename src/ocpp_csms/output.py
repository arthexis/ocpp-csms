from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, TextIO


def iso_datetime(value: datetime | date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.isoformat().replace("+00:00", "Z")
    return value.isoformat()


def _json_default(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return iso_datetime(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def json_command_result(data: Any, *, schema: str) -> dict[str, Any]:
    if not schema:
        raise ValueError("schema must not be empty")
    return {"schema": schema, "data": data}


def emit_json(value: Any, *, stream: TextIO | None = None) -> None:
    target = stream if stream is not None else sys.stdout
    json.dump(value, target, default=_json_default, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    target.write("\n")
