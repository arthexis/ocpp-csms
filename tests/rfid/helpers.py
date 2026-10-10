from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from ocpp_csms.events import EventStore
from ocpp_csms.rfid.cache import RFIDCacheState
from ocpp_csms.rfid.list_query import RFIDListEntrySnapshot, RFIDListSnapshot
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions import TransactionArchive


def start_payload(
    *,
    id_tag: str = "card-a",
    meter_start: int = 1000,
    timestamp: str = "2026-10-06T10:00:00Z",
) -> dict[str, object]:
    return {
        "connector_id": 1,
        "id_tag": id_tag,
        "meter_start": meter_start,
        "timestamp": timestamp,
    }


def stop_payload(
    transaction_id: int,
    *,
    meter_stop: int,
    timestamp: str = "2026-10-06T10:30:00Z",
) -> dict[str, object]:
    return {
        "transaction_id": transaction_id,
        "meter_stop": meter_stop,
        "timestamp": timestamp,
    }


def report_args(tmp_path: Path, tag: str | None = "card-a") -> argparse.Namespace:
    return argparse.Namespace(data_dir=str(tmp_path), rfid_command="report", tag=tag)


def cache_state(
    *,
    version: int = 7,
    entries: tuple[RFIDListEntrySnapshot, ...] = (),
    known: bool = True,
    has_history: bool = True,
    list_hash: str = "hash",
) -> RFIDCacheState:
    snapshot = None
    if known:
        snapshot = RFIDListSnapshot(
            id=1,
            charger_id="charger-a",
            list_version=version,
            sent_at="2026-10-07T04:00:00Z",
            source_file="rfid.csv",
            list_hash=list_hash,
            verified_version=version,
            entries=entries,
        )
    return RFIDCacheState(
        charger_id="charger-a",
        list_version=version,
        has_history=has_history,
        snapshot=snapshot,
    )


def record_list(
    store: EventStore,
    charger: str,
    version: int,
    *,
    name: str | None = None,
    rfid: str = "CARD-A",
    verified: int | None = None,
    source: str | None = "rfid.csv",
) -> int:
    return store.record_rfid_list(
        charger,
        list_version=version,
        entries=[{"rfid": rfid, "name": name, "enabled": True}],
        source_file=source,
        list_hash=f"hash-{charger}-{version}-{rfid}",
        verified_version=version if verified is None else verified,
    )


def make_session(tmp_path: Path) -> tuple[ChargePointSession, list[tuple[object, ...]]]:
    recorded: list[tuple[object, ...]] = []
    session = ChargePointSession(
        "charger-a",
        SimpleNamespace(last_frame="test"),
        TransactionArchive(tmp_path),
        EventStore(tmp_path),
    )

    def record(action, payload, *, direction="in", transaction_id=None):
        recorded.append((action, payload, direction, transaction_id))

    session._record = record
    return session, recorded


def transaction_records(tmp_path: Path) -> list[dict[str, object]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "transactions").glob("*/*.json")
    ]
