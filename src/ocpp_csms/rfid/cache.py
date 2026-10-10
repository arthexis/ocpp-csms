from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from ocpp_csms.control import send_control
from ocpp_csms.rfid.list_query import RFIDListQuery, RFIDListSnapshot


@dataclass(frozen=True)
class RFIDCacheState:
    charger_id: str
    list_version: int
    has_history: bool
    snapshot: RFIDListSnapshot | None

    @property
    def known(self) -> bool:
        return self.snapshot is not None

    @property
    def status(self) -> str:
        if not self.has_history:
            return "no_history"
        return "known" if self.known else "unknown"


async def resolve_rfid_cache(
    data_dir: str | Path,
    *,
    charger: str | None = None,
) -> RFIDCacheState | None:
    """Resolve live charger local-list version against accepted CSMS history.

    No state is returned when there is no uniquely resolvable connected charger,
    the control socket is unavailable, or the charger cannot report a local-list
    version. Database history by itself is deliberately insufficient.
    """
    request: dict[str, object] = {"command": "rfid_version"}
    if charger is not None:
        request["charger"] = charger

    try:
        result = await send_control(data_dir, request)
    except (OSError, ValueError, ConnectionError):
        return None

    if "error" in result:
        return None

    response = result.get("response")
    if not isinstance(response, dict):
        return None

    charger_id = response.get("charger")
    list_version = response.get("list_version")
    if (
        not isinstance(charger_id, str)
        or not charger_id
        or not isinstance(list_version, int)
        or isinstance(list_version, bool)
    ):
        return None

    history = RFIDListQuery(data_dir)
    has_history = history.has_history(charger_id)
    snapshot = history.version(charger_id, list_version) if has_history else None
    return RFIDCacheState(
        charger_id=charger_id,
        list_version=list_version,
        has_history=has_history,
        snapshot=snapshot,
    )


def resolve_rfid_cache_sync(
    data_dir: str | Path,
    *,
    charger: str | None = None,
) -> RFIDCacheState | None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(resolve_rfid_cache(data_dir, charger=charger))
    # The CLI is synchronous. Library callers already inside an event loop should
    # use resolve_rfid_cache() directly instead of nesting asyncio.run().
    return None
