"""data control command family; preserves original request validation order."""
from __future__ import annotations

from typing import Any
from .protocols import Session, SessionRegistry
from ._dispatch_support import _response_payload
from ._dispatch_validation import _non_negative_int, _positive_int

async def dispatch_data(registry: SessionRegistry, session: Session, charger: str, command: str, request: dict[str, Any]) -> dict[str, Any]:
    if command == "data_transfer":
        vendor_id = request.get("vendor_id")
        message_id = request.get("message_id")
        data = request.get("data")
        if not isinstance(vendor_id, str) or not 1 <= len(vendor_id) <= 255:
            return {"error": "invalid_vendor_id"}
        if message_id is not None and (not isinstance(message_id, str) or not 1 <= len(message_id) <= 50):
            return {"error": "invalid_message_id"}
        if data is not None and not isinstance(data, str):
            return {"error": "invalid_data"}
        response = await session.data_transfer(vendor_id, message_id, data)
    elif command == "reserve":
        from datetime import datetime, timezone
        connector = request.get("connector")
        reservation_id = request.get("reservation_id")
        id_tag = request.get("id_tag")
        parent = request.get("parent_id_tag")
        expiry = request.get("expiry_date")
        if not _non_negative_int(connector):
            return {"error": "invalid_connector"}
        if not _positive_int(reservation_id):
            return {"error": "invalid_reservation_id"}
        if not isinstance(id_tag, str) or not 1 <= len(id_tag) <= 20:
            return {"error": "invalid_id_tag"}
        if parent is not None and (not isinstance(parent, str) or not 1 <= len(parent) <= 20):
            return {"error": "invalid_parent_id_tag"}
        if not isinstance(expiry, str):
            return {"error": "invalid_expiry"}
        try:
            when = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if when.tzinfo is None or when <= datetime.now(timezone.utc):
                return {"error": "invalid_expiry"}
        except ValueError:
            return {"error": "invalid_expiry"}
        response = await session.reserve_now(connector, expiry, id_tag, reservation_id, parent)
    elif command == "cancel_reservation":
        reservation_id = request.get("reservation_id")
        if not _positive_int(reservation_id):
            return {"error": "invalid_reservation_id"}
        response = await session.cancel_reservation(reservation_id)
    elif command == "update_firmware":
        from datetime import datetime, timezone
        from urllib.parse import urlsplit
        location = request.get("location")
        retrieve_date = request.get("retrieve_date")
        if not isinstance(location, str) or not location:
            return {"error": "invalid_location"}
        try:
            parsed = urlsplit(location)
            if parsed.scheme.lower() not in {"http", "https", "ftp", "ftps"} or not parsed.hostname or parsed.fragment:
                return {"error": "invalid_location"}
        except ValueError:
            return {"error": "invalid_location"}
        if not isinstance(retrieve_date, str):
            return {"error": "invalid_retrieve_date"}
        try:
            when = datetime.fromisoformat(retrieve_date.replace("Z", "+00:00"))
            if when.tzinfo is None:
                return {"error": "timezone_required"}
        except ValueError:
            return {"error": "invalid_retrieve_date"}
        retries = request.get("retries")
        retry_interval = request.get("retry_interval")
        if retries is not None and not _non_negative_int(retries):
            return {"error": "invalid_retries"}
        if retry_interval is not None and not _non_negative_int(retry_interval):
            return {"error": "invalid_retry_interval"}
        response = await session.update_firmware(
            location, retrieve_date, retries=retries, retry_interval=retry_interval
        )
    elif command == "get_diagnostics":
        from urllib.parse import urlsplit
        from datetime import datetime
        location = request.get("location")
        if not isinstance(location, str) or not location:
            return {"error": "invalid_location"}
        try:
            parsed = urlsplit(location)
            if parsed.scheme.lower() not in {"http", "https", "ftp", "ftps"} or not parsed.hostname or parsed.fragment:
                return {"error": "invalid_location"}
        except ValueError:
            return {"error": "invalid_location"}
        retries = request.get("retries")
        interval = request.get("retry_interval")
        if retries is not None and not _non_negative_int(retries):
            return {"error": "invalid_retries"}
        if interval is not None and not _non_negative_int(interval):
            return {"error": "invalid_retry_interval"}
        start_time, stop_time = request.get("start_time"), request.get("stop_time")
        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00")) if start_time else None
            stop_dt = datetime.fromisoformat(stop_time.replace("Z", "+00:00")) if stop_time else None
            if any(dt.tzinfo is None for dt in (start_dt, stop_dt) if dt is not None):
                return {"error": "timezone_required"}
            if start_dt and stop_dt and start_dt > stop_dt:
                return {"error": "invalid_time_window"}
        except (TypeError, ValueError, AttributeError):
            return {"error": "invalid_time_window"}
        response_timeout = request.get("response_timeout", 30)
        if response_timeout is not None and (type(response_timeout) is not int or response_timeout < 1):
            return {"error": "invalid_response_timeout"}
        response = await session.get_diagnostics(
            location, retries=retries, retry_interval=interval,
            start_time=start_time, stop_time=stop_time,
            response_timeout=response_timeout,
        )
    return {"ok": True, "response": _response_payload(response)}
