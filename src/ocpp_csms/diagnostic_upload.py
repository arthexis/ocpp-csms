"""HTTPS PUT diagnostic drop-box state. No HTTP application server is required."""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

CONFIG = Path("/etc/ocpp-csms/diagnostics-https.json")
TOKEN_RE = re.compile(r"^[a-f0-9]{40}$")
ROOT = Path("/var/lib/ocpp-csms/diagnostics")
MAX_AGE = timedelta(hours=2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _records(data_dir: str | Path) -> Path:
    return Path(data_dir).expanduser() / "diagnostics-requests"


def _save(path: Path, record: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def configuration() -> dict:
    if not CONFIG.is_file():
        raise ValueError("HTTPS diagnostic uploads are not configured; deploy the public nginx expose role")
    settings = json.loads(CONFIG.read_text(encoding="utf-8"))
    host = settings.get("hostname", "")
    if not isinstance(host, str) or not re.fullmatch(r"[a-z0-9.-]+", host):
        raise ValueError("invalid HTTPS diagnostic hostname")
    if not ROOT.is_dir():
        raise ValueError("HTTPS diagnostic upload directory is missing; redeploy expose")
    return settings


def prepare(data_dir: str | Path, charger: str | None) -> tuple[dict, str]:
    settings = configuration()
    token = secrets.token_hex(20)
    directory = ROOT / token
    directory.mkdir(mode=0o2770)
    directory.chmod(0o2770)
    created = _now()
    record = {
        "id": token,
        "charger": charger,
        "created_at": created.isoformat(),
        "expires_at": (created + MAX_AGE).isoformat(),
        "suggested_file": None,
        "result": "requested",
    }
    _save(_records(data_dir) / (token + ".json"), record)
    # nginx only permits a PUT to an existing request directory; unknown IDs fail.
    url = f"https://{settings['hostname']}/ocpp-diagnostics/{token}/upload.bin"
    return record, url


def record_response(data_dir: str | Path, token: str, response: dict) -> None:
    path = _records(data_dir) / (token + ".json")
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["suggested_file"] = response.get("file_name", response.get("fileName"))
    entry["result"] = "acknowledged"
    _save(path, entry)


def list_requests(data_dir: str | Path, *, charger: str | None = None) -> list[dict]:
    folder = _records(data_dir)
    if not folder.is_dir():
        return []
    result = []
    for file in folder.glob("*.json"):
        if not TOKEN_RE.fullmatch(file.stem):
            continue
        try:
            entry = json.loads(file.read_text(encoding="utf-8"))
            directory = ROOT / file.stem
            uploads = [p for p in directory.iterdir() if p.is_file() and not p.is_symlink()] if directory.is_dir() else []
            entry["files"] = [{"name": p.name, "bytes": p.stat().st_size} for p in uploads]
            if uploads:
                entry["result"] = "received"
            elif _now() > datetime.fromisoformat(entry["expires_at"]):
                entry["result"] = "expired"
            if charger is None or entry.get("charger") == charger:
                # Never expose bearer token in ordinary output.
                result.append(entry)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(result, key=lambda r: r["created_at"], reverse=True)


def remove_expired(data_dir: str | Path) -> int:
    """Remove expired upload directories and metadata; call from maintenance."""
    import shutil
    count = 0
    for entry in list_requests(data_dir):
        if _now() <= datetime.fromisoformat(entry["expires_at"]):
            continue
        token = entry["id"]
        if not TOKEN_RE.fullmatch(token):
            continue
        shutil.rmtree(ROOT / token, ignore_errors=True)
        (_records(data_dir) / (token + ".json")).unlink(missing_ok=True)
        count += 1
    return count


def inspect_file(path: Path, *, max_members: int = 32, max_read: int = 262144) -> list[str]:
    """Bounded, non-executing log inspection of a received archive or plain text."""
    import io
    import tarfile
    import zipfile

    terms = ("fault", "error", "alarm", "emergency", "failure")
    output: list[str] = []

    def scan(name: str, data: bytes) -> None:
        if len(output) >= 20:
            return
        if b"\\x00" in data[:256]:
            return
        for line in data.decode("utf-8", errors="replace").splitlines():
            if any(term in line.lower() for term in terms):
                output.append(f"{name}: {line[:240]}")
                if len(output) >= 20:
                    break

    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist()[:max_members]:
                    if member.is_dir() or member.file_size > max_read:
                        continue
                    with archive.open(member) as stream:
                        scan(member.filename, stream.read(max_read))
        elif tarfile.is_tarfile(path):
            with tarfile.open(path, "r:*") as archive:
                for member in archive.getmembers()[:max_members]:
                    if not member.isfile() or member.size > max_read:
                        continue
                    stream = archive.extractfile(member)
                    if stream is not None:
                        with stream:
                            scan(member.name, stream.read(max_read))
        elif path.stat().st_size <= max_read:
            scan(path.name, path.read_bytes())
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile):
        return ["Unable to inspect archive; original file preserved."]
    return output
