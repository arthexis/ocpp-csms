"""Conservative, offline restoration of a verified pre-reset archive."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile

from ocpp_csms.runtime import process_is_running
from ocpp_csms.schema import inspect_schema, require_supported_schema


def _files(root: Path) -> dict[str, str]:
    """Hash a consistent SQLite copy and the other regular evidence files."""
    result: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="ocpp-undo-check-") as scratch:
        info = inspect_schema(root)
        db_rel = info.path.relative_to(root) if info.exists else None
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("symlinked evidence: undo refused")
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if db_rel is not None and rel in {db_rel.as_posix(), db_rel.as_posix() + "-wal", db_rel.as_posix() + "-shm"}:
                continue
            result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        if db_rel is not None:
            target = Path(scratch) / "database.sqlite3"
            with closing(sqlite3.connect(f"file:{info.path}?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(target)) as dst:
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                        raise ValueError("invalid current database")
            # SQLite file fingerprints vary across consistent backups; compare
            # logical content rather than byte-level page layout.
            with closing(sqlite3.connect(target)) as db:
                result["@sqlite"] = hashlib.sha256("\n".join(db.iterdump()).encode()).hexdigest()
    return result


def record_reset_state(root: Path, archive: Path) -> None:
    """Record the post-reset state; refuse undo if anything changes later."""
    state = archive.with_suffix(archive.suffix + ".post-reset.json")
    temp = state.with_suffix(state.suffix + ".partial")
    try:
        temp.write_text(json.dumps({"format": 1, "files": _files(root)}, sort_keys=True), encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, state)
    finally:
        temp.unlink(missing_ok=True)


def undo_reset(root: Path, archive: Path, *, dry_run: bool = False) -> dict:
    root = root.expanduser().absolute()
    archive = archive.expanduser().absolute()
    expected_parent = root.parent / (root.name + "-test-backups")
    if root.is_symlink() or not root.is_dir() or archive.is_symlink() or archive.parent != expected_parent:
        raise ValueError("unsafe reset restore paths")
    if process_is_running(root):
        raise ValueError("CSMS process running; stop service before undo")
    try:
        active = subprocess.run(["systemctl", "is-active", "--quiet", "ocpp-csms.service"],
                                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        active = None
    if active is not None and active.returncode == 0:
        raise ValueError("ocpp-csms.service active; stop before undo")
    state = archive.with_suffix(archive.suffix + ".post-reset.json")
    if not state.is_file():
        raise ValueError("missing post-reset state; refusing unverified undo")
    post = json.loads(state.read_text(encoding="utf-8"))
    if post.get("format") != 1 or post.get("files") != _files(root):
        raise ValueError("new or changed evidence since reset; undo refused")
    with tempfile.TemporaryDirectory(prefix="ocpp-undo-", dir=root.parent) as scratch:
        staged = Path(scratch) / "data"
        staged.mkdir()
        with tarfile.open(archive, "r:gz") as tar:
            manifest_stream = tar.extractfile("manifest.json")
            if manifest_stream is None:
                raise ValueError("missing archive manifest")
            manifest = json.load(manifest_stream)
            if manifest.get("format") != 1 or manifest.get("scope") not in {"all", "cp"}:
                raise ValueError("invalid reset archive")
            expected = manifest.get("files")
            if not isinstance(expected, dict):
                raise ValueError("invalid archive checksums")
            for name, digest in expected.items():
                rel = Path(name)
                if rel.is_absolute() or ".." in rel.parts or not rel.parts or not isinstance(digest, str):
                    raise ValueError("unsafe archive member")
                member = tar.getmember("data/" + name)
                if not member.isfile():
                    raise ValueError("unsafe archive file type")
                stream = tar.extractfile(member)
                if stream is None:
                    raise ValueError("missing archive member")
                target = staged / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                sha = hashlib.sha256()
                with target.open("wb") as out:
                    while chunk := stream.read(1024 * 1024):
                        sha.update(chunk)
                        out.write(chunk)
                if sha.hexdigest() != digest:
                    raise ValueError("archive checksum mismatch")
        info = inspect_schema(staged)
        require_supported_schema(info)
        with closing(sqlite3.connect(info.path)) as db:
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("restored database integrity check failed")
        if dry_run:
            return {"decision": "dry-run", "archive": str(archive), "scope": manifest["scope"]}
        displaced = root.parent / (root.name + "-undo-displaced")
        if displaced.exists() or displaced.is_symlink():
            raise ValueError("undo displacement directory already exists")
        root.rename(displaced)
        try:
            staged.rename(root)
        except Exception:
            displaced.rename(root)
            raise
        return {"decision": "undo", "archive": str(archive), "restored": str(root), "displaced": str(displaced)}
