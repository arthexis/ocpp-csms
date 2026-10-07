from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ForwarderState:
    source_id: str | None = None
    cursor: int = 0
    last_success_at: str | None = None
    last_error: str | None = None


class StateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def load(self) -> ForwarderState:
        if not self.path.exists():
            return ForwarderState()
        value = json.loads(self.path.read_text(encoding="utf-8"))
        return ForwarderState(
            source_id=value.get("source_id"),
            cursor=int(value.get("cursor", 0)),
            last_success_at=value.get("last_success_at"),
            last_error=value.get("last_error"),
        )

    def save(self, state: ForwarderState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(asdict(state), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
