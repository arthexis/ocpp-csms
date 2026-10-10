from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

RFID_AUTH_FILENAME = "rfid.csv"
_COLUMNS = ("rfid", "name", "enabled")


@dataclass(frozen=True)
class RFIDEntry:
    rfid: str
    name: str | None = None
    enabled: bool = True


@dataclass(frozen=True)
class RFIDAuthorization:
    source: Path | None
    entries: dict[str, RFIDEntry]
    valid: bool
    error: str | None = None

    @property
    def allow_all(self) -> bool:
        return self.source is None

    def status(self, rfid: str) -> str:
        if self.allow_all:
            return "Accepted"
        if not self.valid:
            return "Blocked"
        entry = self.entries.get(rfid.strip())
        if entry is None:
            return "Invalid"
        return "Accepted" if entry.enabled else "Blocked"


def _enabled(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"", "true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    raise ValueError(f"invalid enabled value: {value!r}")


def _meaningful_rows(path: Path) -> list[list[str]]:
    rows: list[list[str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            if not row or not any(cell.strip() for cell in row):
                continue
            if row[0].lstrip().startswith("#"):
                continue
            rows.append([cell.strip() for cell in row])
    return rows


def _header_map(row: list[str]) -> dict[str, int] | None:
    normalized = [cell.strip().lower() for cell in row]
    if "rfid" not in normalized:
        return None
    if any(column not in _COLUMNS for column in normalized):
        unknown = next(column for column in normalized if column not in _COLUMNS)
        raise ValueError(f"unknown RFID authorization column: {unknown}")
    if len(set(normalized)) != len(normalized):
        raise ValueError("duplicate RFID authorization header column")
    return {column: index for index, column in enumerate(normalized)}


def load_rfid_authorization(data_dir: str | Path) -> RFIDAuthorization:
    path = Path(data_dir).expanduser() / RFID_AUTH_FILENAME
    if not path.exists():
        return RFIDAuthorization(None, {}, True)

    try:
        rows = _meaningful_rows(path)
        if not rows:
            return RFIDAuthorization(path, {}, True)

        header = _header_map(rows[0])
        data_rows = rows[1:] if header is not None else rows
        entries: dict[str, RFIDEntry] = {}

        for number, row in enumerate(data_rows, start=2 if header is not None else 1):
            if header is None:
                if len(row) > 3:
                    raise ValueError(f"line {number}: expected at most 3 columns")
                values = row + [""] * (3 - len(row))
                rfid, name, enabled = values
            else:
                if len(row) > len(header):
                    raise ValueError(f"line {number}: too many columns")
                values = row + [""] * (len(header) - len(row))
                rfid = values[header["rfid"]]
                name = values[header["name"]] if "name" in header else ""
                enabled = values[header["enabled"]] if "enabled" in header else ""

            rfid = rfid.strip()
            if not rfid:
                raise ValueError(f"line {number}: RFID is empty")
            if rfid in entries:
                raise ValueError(f"line {number}: duplicate RFID {rfid!r}")
            entries[rfid] = RFIDEntry(
                rfid=rfid,
                name=name.strip() or None,
                enabled=_enabled(enabled),
            )

        return RFIDAuthorization(path, entries, True)
    except (OSError, csv.Error, UnicodeError, ValueError) as exc:
        return RFIDAuthorization(path, {}, False, str(exc))


def authorize_rfid(data_dir: str | Path, rfid: str) -> str:
    return load_rfid_authorization(data_dir).status(rfid)
