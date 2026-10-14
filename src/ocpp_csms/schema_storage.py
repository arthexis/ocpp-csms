"""Schema-versioned storage planning; deployment activation belongs to Ansible.

All inspection is read-only. A schema transition prepares a fresh directory,
never upgrades, deletes, or reopens historical evidence.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from ocpp_csms.schema import CURRENT_SCHEMA_VERSION, DATABASE_FILENAME, SchemaInfo, create_current_schema, inspect_schema, database_path


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


def discover_active_storage(root: str | Path) -> Path | None:
    """Discover active versioned storage or the original unversioned layout.

    Discovery never follows a broken activation pointer or silently falls back
    to legacy storage when a current pointer exists.
    """
    root = Path(root).expanduser()
    current = root / "current"
    if current.is_symlink():
        target = current.resolve(strict=True)
        if not target.is_dir() or not target.is_relative_to((root / "schemas").resolve()):
            raise RuntimeError(f"invalid active schema pointer: {current}")
        return target
    if current.exists():
        raise RuntimeError(f"active schema pointer is not a symlink: {current}")
    legacy = database_path(root)
    if legacy.exists():
        return root
    if (root / "schemas").exists():
        raise RuntimeError("versioned storage exists without an active current pointer")
    return None


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
    if plan.target.name != str(CURRENT_SCHEMA_VERSION):
        raise RuntimeError("cannot prepare a schema other than the current version")
    if plan.action != "create":
        raise ValueError(f"unknown storage action: {plan.action}")
    if plan.target.exists() or plan.target.is_symlink():
        raise RuntimeError(f"target schema directory already exists: {plan.target}")
    # create_current_schema is exclusive about the SQLite file; directory-level
    # exclusivity is enforced above. Deployment must serialize preparation.
    # Reserve the directory exclusively so concurrent preparations cannot reuse it.
    plan.target.mkdir(parents=True, exist_ok=False)
    return create_current_schema(plan.target)


def adopt_legacy_storage(root: str | Path) -> Path:
    """Move stopped, checkpointed legacy storage into its version directory.

    Reject ambiguous or partially adopted layouts; never copy SQLite while live.
    The caller must enforce service stop and WAL checkpoint before invoking.
    """
    root = Path(root).expanduser()
    if (root / "current").exists() or (root / "current").is_symlink():
        raise RuntimeError("legacy adoption requires no active current pointer")
    info = inspect_schema(root)
    if not info.exists or info.version is None or info.version < 1:
        raise RuntimeError("legacy database missing or schema invalid")
    target = schema_directory(root, info.version)
    if target.exists() or target.is_symlink():
        raise RuntimeError(f"legacy schema destination already exists: {target}")
    # Reject sidecars after checkpoint: they may contain uncheckpointed evidence.
    if any((root / (DATABASE_FILENAME + suffix)).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise RuntimeError("legacy SQLite sidecar remains after checkpoint")
    entries = [root / DATABASE_FILENAME, root / "transactions"]
    if not entries[1].is_dir() or entries[1].is_symlink():
        raise RuntimeError("legacy transaction archive missing or unsafe")
    if any(entry.is_symlink() for entry in entries):
        raise RuntimeError("legacy storage must not contain symlinks")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(exist_ok=False)
    try:
        for entry in entries:
            entry.rename(target / entry.name)
    except OSError as exc:
        # Do not guess whether partial moves can be undone safely.
        raise RuntimeError(f"legacy adoption interrupted; manual recovery required: {exc}") from exc
    return target


def activate_schema(root: str | Path, target: str | Path) -> Path:
    """Atomically select an existing, verified schema directory."""
    root = Path(root).expanduser()
    target = Path(target).expanduser().resolve(strict=True)
    schemas = (root / "schemas").resolve(strict=True)
    if target.parent != schemas or not target.is_dir() or not target.name.isdecimal():
        raise RuntimeError("candidate must be a direct versioned schema directory")
    info = inspect_schema(target)
    if not info.exists or info.version != int(target.name):
        raise RuntimeError("candidate schema does not match its directory")
    current = root / "current"
    if current.exists() and not current.is_symlink():
        raise RuntimeError("current pointer is not a symlink")
    if current.is_symlink():
        active = current.resolve(strict=True)
        if active == target:
            return current
        if not active.is_dir() or active.parent != schemas:
            raise RuntimeError("existing current pointer is unsafe")
    temporary = root / (".current-" + uuid.uuid4().hex)
    temporary.symlink_to(Path("schemas") / target.name, target_is_directory=True)
    try:
        os.replace(temporary, current)
    finally:
        temporary.unlink(missing_ok=True)
    return current
