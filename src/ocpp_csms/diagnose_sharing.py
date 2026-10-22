"""Explicit read-only nginx sharing for received diagnostic archives.

The nginx site and TLS certificate are provisioned separately by Ansible.
Mutations require root; there is no permanently running application process.
"""
from __future__ import annotations

import json
import grp
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

from ocpp_csms.diagnostic_upload import ROOT, TOKEN_RE

HOST_CONFIG = Path("/etc/ocpp-csms/diagnose-download.json")
NGINX_SNIPPETS = Path("/etc/nginx/ocpp-csms-diagnose-download")
AUTH_DIR = Path("/etc/ocpp-csms/diagnose-download")
PUBLIC_ROOT = Path("/var/lib/ocpp-csms/diagnose-downloads")
CP_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")


def _cp(cp: str) -> str:
    if not CP_RE.fullmatch(cp):
        raise ValueError("invalid charge point ID for diagnostic sharing")
    return cp


def hostname() -> str:
    try:
        value = json.loads(HOST_CONFIG.read_text(encoding="utf-8"))["hostname"]
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError("diagnostic download site not provisioned; deploy expose with the download hostname") from exc
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9.-]+", value):
        raise ValueError("invalid diagnostic download hostname")
    return value


def _root_required() -> None:
    if os.geteuid() != 0:
        raise PermissionError("diagnose serve/revoke requires sudo to update nginx credentials")


def _run(*cmd: str, input: str | None = None) -> str:
    result = subprocess.run(cmd, input=input, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _archive_files(data_dir: str | Path, cp: str) -> list[Path]:
    records = Path(data_dir).expanduser() / "diagnostics-requests"
    found = []
    if not records.is_dir():
        return found
    for record in records.glob("*.json"):
        if not TOKEN_RE.fullmatch(record.stem) or record.is_symlink():
            continue
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if data.get("charger") != cp:
            continue
        folder = ROOT / record.stem
        if not folder.is_dir() or folder.is_symlink():
            continue
        for candidate in sorted(folder.iterdir()):
            if candidate.is_file() and not candidate.is_symlink():
                found.append(candidate)
    return found


def _replace_file(path: Path, content: str, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".new-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
        os.chmod(name, mode)
        os.chown(name, 0, grp.getgrnam("www-data").gr_gid)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _reload() -> None:
    _run("nginx", "-t")
    _run("systemctl", "reload", "nginx")


def status(cp: str) -> dict:
    cp = _cp(cp)
    active = (NGINX_SNIPPETS / f"{cp}.conf").is_file() and (AUTH_DIR / f"{cp}.htpasswd").is_file()
    return {"charger": cp, "enabled": active, "url": f"https://{hostname()}/{cp}/" if active else None}


def serve(data_dir: str | Path, cp: str) -> dict:
    cp = _cp(cp)
    host = hostname()
    _root_required()
    if not NGINX_SNIPPETS.is_dir() or not PUBLIC_ROOT.is_dir():
        raise ValueError("diagnostic download nginx site is not installed")
    sources = _archive_files(data_dir, cp)
    if not sources:
        raise ValueError(f"no retained diagnostic archives for {cp}")
    password = secrets.token_urlsafe(24)
    hashed = _run("openssl", "passwd", "-6", "-stdin", input=password + "\n")
    destination = PUBLIC_ROOT / cp
    pending = Path(tempfile.mkdtemp(prefix=".publish-", dir=PUBLIC_ROOT))
    backup = PUBLIC_ROOT / f".{cp}.previous"
    conf = NGINX_SNIPPETS / f"{cp}.conf"
    auth = AUTH_DIR / f"{cp}.htpasswd"
    previous_conf = conf.read_text(encoding="utf-8") if conf.exists() else None
    previous_auth = auth.read_text(encoding="utf-8") if auth.exists() else None
    try:
        os.chmod(pending, 0o750)
        os.chown(pending, 0, grp.getgrnam("www-data").gr_gid)
        for index, source in enumerate(sources, 1):
            with source.open("rb") as stream:
                ext = ".zip" if stream.read(4) == bytes((80, 75, 3, 4)) else ".bin"
            target = pending / f"diagnostic-{index:03d}{ext}"
            shutil.copyfile(source, target)
            os.chmod(target, 0o640)
            os.chown(target, 0, grp.getgrnam("www-data").gr_gid)
        AUTH_DIR.mkdir(parents=True, exist_ok=True, mode=0o750)
        _replace_file(auth, f"{cp}:{hashed}\n")
        # Separate exact location and credentials prevent one CP from browsing another.
        snippet = (
            f"location ^~ /{cp}/ {{\n"
            f"    alias {PUBLIC_ROOT}/{cp}/;\n"
            "    autoindex on;\n"
            '    auth_basic "OCPP diagnostics";\n'
            f"    auth_basic_user_file {auth};\n"
            "    limit_except GET { deny all; }\n"
            '    add_header Cache-Control "private, no-store" always;\n'
            "}\n"
        )
        _replace_file(conf, snippet, 0o644)
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            destination.rename(backup)
        pending.rename(destination)
        _reload()
    except Exception:
        if previous_conf is None:
            conf.unlink(missing_ok=True)
        else:
            _replace_file(conf, previous_conf, 0o644)
        if previous_auth is None:
            auth.unlink(missing_ok=True)
        else:
            _replace_file(auth, previous_auth)
        if destination.exists():
            shutil.rmtree(destination)
        if backup.exists():
            backup.rename(destination)
        try:
            _reload()
        except Exception:
            pass
        raise
    finally:
        if pending.exists():
            shutil.rmtree(pending)
    if backup.exists():
        shutil.rmtree(backup)
    return {"charger": cp, "url": f"https://{host}/{cp}/", "password": password, "files": len(sources)}


def revoke(cp: str) -> dict:
    cp = _cp(cp)
    hostname()
    _root_required()
    conf = NGINX_SNIPPETS / f"{cp}.conf"
    auth = AUTH_DIR / f"{cp}.htpasswd"
    # Remove credentials before reloading so access fails closed.
    auth.unlink(missing_ok=True)
    conf.unlink(missing_ok=True)
    _reload()
    directory = PUBLIC_ROOT / cp
    if directory.exists():
        shutil.rmtree(directory)
    return {"charger": cp, "enabled": False}
