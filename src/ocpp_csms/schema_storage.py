"""Deterministic storage for the schema compiled into this CSMS release."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ocpp_csms.schema import CURRENT_SCHEMA_VERSION, SchemaInfo, create_current_schema, inspect_schema


@dataclass(frozen=True)
class StoragePlan:
    action: str
    active: Path | None
    target: Path
    active_schema: int | None


def schema_directory(root: str | Path, version: int = CURRENT_SCHEMA_VERSION) -> Path:
    if version < 1:
        raise ValueError("schema version must be positive")
    return Path(root).expanduser() / "schemas" / str(version)


def plan_storage(root: str | Path, active: str | Path | None = None) -> StoragePlan:
    """Select only the compiled schema path; leave historical storage untouched."""
    root = Path(root).expanduser()
    target = schema_directory(root)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir():
            raise RuntimeError(f"unsafe schema path: {target}")
        info = inspect_schema(target)
        if not info.exists or info.version != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(f"interrupted or invalid schema preparation: {target}")
        return StoragePlan("reuse", target, target, CURRENT_SCHEMA_VERSION)
    return StoragePlan("create", None, target, None)


def prepare_storage(plan: StoragePlan) -> SchemaInfo:
    """Create only the statically compiled schema, without modifying existing databases."""
    if plan.target.name != str(CURRENT_SCHEMA_VERSION):
        raise RuntimeError("cannot prepare a schema other than the compiled version")
    if plan.action == "reuse":
        return inspect_schema(plan.target)
    if plan.action != "create":
        raise ValueError(f"unknown storage action: {plan.action}")
    if plan.target.exists() or plan.target.is_symlink():
        raise RuntimeError(f"schema target already exists: {plan.target}")
    plan.target.mkdir(parents=True, exist_ok=False)
    return create_current_schema(plan.target)
