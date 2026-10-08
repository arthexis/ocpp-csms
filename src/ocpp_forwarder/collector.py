from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class CollectorError(RuntimeError):
    pass


class CollectorClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout = timeout

    def _request(
        self,
        method: str,
        resource: str,
        *,
        rows: list[dict[str, Any]] | dict[str, Any],
        on_conflict: str | None = None,
        prefer: str | None = None,
        query: dict[str, str] | None = None,
    ) -> None:
        params = dict(query or {})
        if on_conflict:
            params["on_conflict"] = on_conflict
        url = f"{self.base_url}/{resource}"
        if params:
            url += "?" + urlencode(params)
        data = json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()
        request = Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                **({"Prefer": prefer} if prefer else {}),
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise CollectorError(f"Collector returned HTTP {response.status}")
        except HTTPError as exc:
            raise CollectorError(f"Collector returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise CollectorError(f"Collector unavailable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise CollectorError("Collector request timed out") from exc

    def upsert(
        self,
        resource: str,
        rows: list[dict[str, Any]],
        *,
        on_conflict: str,
    ) -> None:
        if not rows:
            return
        self._request(
            "POST",
            resource,
            rows=rows,
            on_conflict=on_conflict,
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def update_satellite(self, satellite_id: str, values: dict[str, Any]) -> None:
        self._request(
            "PATCH",
            "satellites",
            rows=values,
            query={"satellite_id": f"eq.{satellite_id}"},
            prefer="return=minimal",
        )
