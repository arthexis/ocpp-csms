"""Deterministic storage for the schema compiled into this CSMS release."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ocpp_csms.schema import CURRENT_SCHEMA_VERSION, SchemaInfo, create_current_schema, inspect_schema, database_path


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
    """Always select the compiled schema path; refuse downgrades and ambiguous data."""
    root = Path(root).expanduser()
    target = schema_directory(root)
    if (root / "current").exists() or (root / "current").is_symlink():
        raise RuntimeError("obsolete storage pointer requires manual resolution")
    versions: list[int] = []
    schemas = root / "schemas"
    if schemas.exists():
        if not schemas.is_dir() or schemas.is_symlink():
            raise RuntimeError("invalid schemas directory")
        for child in schemas.iterdir():
            if not child.is_dir() or child.is_symlink() or not child.name.isdecimal():
                raise RuntimeError(f"invalid schema storage directory: {child}")
            info = inspect_schema(child)
            if info.exists:
                if info.version != int(child.name):
                    raise RuntimeError(f"schema directory/database mismatch: {child}")
                versions.append(info.version)
            elif child != target:
                raise RuntimeError(f"missing database in schema directory: {child}")
    if database_path(root).exists():
        raise RuntimeError("unversioned database requires manual resolution; will not move or overwrite data")
    if active is not None:
        active_path = Path(active).expanduser()
        if active_path != target:
            info = inspect_schema(active_path)
            if info.exists:
                raise RuntimeError("noncanonical active database requires manual resolution")
    if any(version > CURRENT_SCHEMA_VERSION for version in versions):
        raise RuntimeError(f"schema downgrade rejected: installed schema is newer than {CURRENT_SCHEMA_VERSION}")
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir():
            raise RuntimeError(f"unsafe schema path: {target}")
        info = inspect_schema(target)
        if not info.exists or info.version != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(f"interrupted or invalid schema preparation: {target}")
        return StoragePlan("reuse", target, target, CURRENT_SCHEMA_VERSION)
    return StoragePlan("create", None, target, max(versions, default=None))


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
