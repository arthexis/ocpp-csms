from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ocpp_csms.schema import database_path


@dataclass(frozen=True)
class RFIDListEntrySnapshot:
    rfid: str
    name: str | None
    enabled: bool


@dataclass(frozen=True)
class RFIDListSnapshot:
    id: int
    charger_id: str
    list_version: int
    sent_at: str
    source_file: str | None
    list_hash: str
    verified_version: int | None
    entries: tuple[RFIDListEntrySnapshot, ...]


class RFIDListQuery:
    """Read accepted charger-local RFID list history without mutating SQLite."""

    def __init__(self, data_dir: str | Path) -> None:
        self.path = database_path(data_dir)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)

    def list(self, *, charger: str | None = None) -> list[RFIDListSnapshot]:
        if not self.path.exists():
            return []

        sql = """
            SELECT id, charger_id, list_version, sent_at,
                   source_file, list_hash, verified_version
            FROM rfid_lists
        """
        values: tuple[object, ...] = ()
        if charger is not None:
            sql += " WHERE charger_id = ?"
            values = (charger,)
        sql += " ORDER BY sent_at DESC, id DESC"

        with self._connect() as connection:
            rows = connection.execute(sql, values).fetchall()
            return [self._snapshot(connection, row) for row in rows]

    def latest(self, charger: str) -> RFIDListSnapshot | None:
        snapshots = self.list(charger=charger)
        return snapshots[0] if snapshots else None

    def version(self, charger: str, list_version: int) -> RFIDListSnapshot | None:
        if not self.path.exists():
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, charger_id, list_version, sent_at,
                       source_file, list_hash, verified_version
                FROM rfid_lists
                WHERE charger_id = ? AND list_version = ?
                ORDER BY sent_at DESC, id DESC
                LIMIT 1
                """,
                (charger, int(list_version)),
            ).fetchone()
            return self._snapshot(connection, row) if row else None

    def has_history(self, charger: str) -> bool:
        if not self.path.exists():
            return False
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM rfid_lists WHERE charger_id = ? LIMIT 1",
                (charger,),
            ).fetchone()
        return row is not None

    @staticmethod
    def _snapshot(
        connection: sqlite3.Connection,
        row: tuple[object, ...],
    ) -> RFIDListSnapshot:
        list_id = int(row[0])
        entries = tuple(
            RFIDListEntrySnapshot(
                rfid=str(entry[0]),
                name=str(entry[1]) if entry[1] is not None else None,
                enabled=bool(entry[2]),
            )
            for entry in connection.execute(
                """
                SELECT rfid, name, enabled
                FROM rfid_list_entries
                WHERE list_id = ?
                ORDER BY rfid
                """,
                (list_id,),
            )
        )
        return RFIDListSnapshot(
            id=list_id,
            charger_id=str(row[1]),
            list_version=int(row[2]),
            sent_at=str(row[3]),
            source_file=str(row[4]) if row[4] is not None else None,
            list_hash=str(row[5]),
            verified_version=int(row[6]) if row[6] is not None else None,
            entries=entries,
        )
