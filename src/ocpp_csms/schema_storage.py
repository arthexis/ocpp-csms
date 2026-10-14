"""Schema-versioned storage planning; deployment activation belongs to Ansible.

All inspection is read-only. A schema transition prepares a fresh directory,
never upgrades, deletes, or reopens historical evidence.
"""
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


def schema_directory(root: str | Path, version: int) -> Path:
    if version < 1:
        raise ValueError("schema version must be positive")
    return Path(root).expanduser() / "schemas" / str(version)


def plan_storage(root: str | Path, active: str | Path | None,
                 *, candidate_version: int = CURRENT_SCHEMA_VERSION) -> StoragePlan:
    """Plan without modifying either the active or target database.

    An existing target directory is rejected even if its SQLite file is absent:
    interrupted preparation must be investigated, not overwritten.
    """
    target = schema_directory(root, candidate_version)
    active_path = Path(active).expanduser() if active is not None else None
    info: SchemaInfo | None = inspect_schema(active_path) if active_path is not None else None
    if info is not None and not info.exists:
        raise RuntimeError(f"active database missing: {info.path}")
    version = info.version if info is not None else None
    if version is not None and version < 1:
        raise RuntimeError(f"active database has invalid schema version: {version}")
    if version is not None and candidate_version < version:
        raise RuntimeError(f"schema downgrade rejected: {version} to {candidate_version}")
    if version == candidate_version:
        return StoragePlan("reuse", active_path, active_path, version)
    if target.exists() or target.is_symlink():
        raise RuntimeError(f"target schema directory already exists: {target}")
    return StoragePlan("create", active_path, target, version)


def prepare_storage(plan: StoragePlan) -> SchemaInfo:
    """Prepare only a new candidate; never change active storage or activation links."""
    if plan.action == "reuse":
        return inspect_schema(plan.target)
    if plan.action != "create":
        raise ValueError(f"unknown storage action: {plan.action}")
    if plan.target.exists() or plan.target.is_symlink():
        raise RuntimeError(f"target schema directory already exists: {plan.target}")
    # create_current_schema is exclusive about the SQLite file; directory-level
    # exclusivity is enforced above. Deployment must serialize preparation.
    # Reserve the directory exclusively so concurrent preparations cannot reuse it.\n    plan.target.mkdir(parents=True, exist_ok=False)\n    return create_current_schema(plan.target)
