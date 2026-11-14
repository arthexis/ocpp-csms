"""rfid control command family; preserves original request validation order."""
from __future__ import annotations

from typing import Any
from .protocols import Session, SessionRegistry
from ._dispatch_support import _response_payload, _rfid_configuration, _send_rfid_list
from ._dispatch_validation import _rfid_entries, _INVALID

async def dispatch_rfid(registry: SessionRegistry, session: Session, charger: str, command: str, request: dict[str, Any]) -> dict[str, Any]:
    if command == "rfid_version":
        response = await session.get_local_list_version()
        payload = _response_payload(response)
        payload["charger"] = charger
        payload["configuration"] = await _rfid_configuration(session)
        return {"ok": True, "response": payload}
    elif command == "rfid_export":
        entries = _rfid_entries(request)
        if entries is _INVALID:
            return {"error": "invalid_rfid_entries"}
        source_file = request.get("source_file")
        list_hash = request.get("list_hash")
        if source_file is not None and (not isinstance(source_file, str) or not source_file):
            return {"error": "invalid_source_file"}
        if not isinstance(list_hash, str) or not list_hash:
            return {"error": "invalid_list_hash"}
        result = await _send_rfid_list(
            registry,
            session,
            charger,
            entries=entries,
            source_file=source_file,
            list_hash=list_hash,
            clear=False,
        )
        return {"ok": True, "response": result} if "error" not in result else result
    elif command == "rfid_clear":
        result = await _send_rfid_list(
            registry,
            session,
            charger,
            entries=[],
            source_file=None,
            list_hash="empty",
            clear=True,
        )
        return {"ok": True, "response": result} if "error" not in result else result
