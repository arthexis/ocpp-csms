from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile


@dataclass(frozen=True)
class FieldConfig:
    charger: str
    legacy_service: str
    csms_service: str
    listener_host: str
    listener_port: int
    csms_data_dir: str
    control_socket: str


@dataclass(frozen=True)
class FieldState:
    config: FieldConfig
    phase: str = "created"
    watchdog: str = "disabled"
    rollback: str = "available"

    def to_dict(self) -> dict:
        return {
            "config": asdict(self.config),
            "phase": self.phase,
            "watchdog": self.watchdog,
            "rollback": self.rollback,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "FieldState":
        return cls(
            config=FieldConfig(**value["config"]),
            phase=value.get("phase", "created"),
            watchdog=value.get("watchdog", "disabled"),
            rollback=value.get("rollback", "available"),
        )


def state_path(run_dir: Path) -> Path:
    return run_dir / "state.json"


def load_state(run_dir: Path) -> FieldState:
    return FieldState.from_dict(json.loads(state_path(run_dir).read_text()))


def save_state(run_dir: Path, state: FieldState) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    target = state_path(run_dir)
    with NamedTemporaryFile("w", dir=run_dir, prefix=".state-", delete=False) as handle:
        json.dump(state.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, target)
