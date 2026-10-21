"""Portable, verified pre-reset snapshots for CSMS evidence."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile
import uuid

from ocpp_csms.schema import inspect_schema


def archive_before_reset(root: Path, *, scope: str, cp: str | None = None) -> Path:
    """Snapshot SQLite through its backup API and package all other regular files.

    A verified archive is published atomically before the caller changes evidence.
    On error no completed archive is published and the caller must not reset.
    """
    root = root.expanduser().absolute()
    backup_dir = root.parent / (root.name + "-test-backups")
    if backup_dir.is_symlink():
        raise ValueError("unsafe backup directory")
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8] + ".tar.gz"
    final = backup_dir / name
    temp = backup_dir / ("." + name + ".partial")
    with tempfile.TemporaryDirectory(prefix="snapshot-", dir=backup_dir) as scratch:
        snapshot = Path(scratch)
        info = inspect_schema(root)
        db_rel = info.path.relative_to(root) if info.exists else None
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("symlinked evidence: backup refused")
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if db_rel is not None and (rel == db_rel or str(rel) in (str(db_rel) + "-wal", str(db_rel) + "-shm")):
                continue
            destination = snapshot / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        if db_rel is not None:
            target = snapshot / db_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(f"file:{info.path}?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(target)) as dst:
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                        raise ValueError("database backup integrity check failed")
        checksums = {}
        for path in sorted(snapshot.rglob("*")):
            if path.is_file():
                checksums[path.relative_to(snapshot).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = {"format": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                    "scope": scope, "cp": cp, "schema": info.version if info.exists else None,
                    "files": checksums}
        try:
            with tarfile.open(temp, "w:gz") as archive:
                payload = json.dumps(manifest, sort_keys=True, indent=2).encode()
                meta = tarfile.TarInfo("manifest.json")
                meta.size = len(payload)
                archive.addfile(meta, io.BytesIO(payload))
                for rel in checksums:
                    archive.add(snapshot / rel, arcname="data/" + rel, recursive=False)
            with tarfile.open(temp, "r:gz") as archive:
                stored = json.load(archive.extractfile("manifest.json"))
                if stored != manifest:
                    raise ValueError("backup manifest mismatch")
                for rel, expected in checksums.items():
                    stream = archive.extractfile("data/" + rel)
                    if stream is None or hashlib.sha256(stream.read()).hexdigest() != expected:
                        raise ValueError("backup checksum mismatch: " + rel)
            os.chmod(temp, 0o600)
            os.replace(temp, final)
        finally:
            temp.unlink(missing_ok=True)
    return final
