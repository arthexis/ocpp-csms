"""Shared validation for charger-initiated file transfers.

Keep command-specific error text at call sites so CLI contracts remain stable.
"""
from __future__ import annotations

from urllib.parse import urlsplit


def validate_transfer_url(location: str, *, error: str) -> None:
    try:
        parts = urlsplit(location)
        if (
            parts.scheme.lower() not in {"http", "https", "ftp", "ftps"}
            or not parts.hostname
            or parts.fragment
        ):
            raise ValueError(error)
    except ValueError as exc:
        raise ValueError(error) from exc


def validate_retry_options(retries: int | None, retry_interval: int | None) -> None:
    if retries is not None and retries < 0:
        raise ValueError("--retries must be non-negative")
    if retry_interval is not None and retry_interval < 0:
        raise ValueError("--retry-interval must be non-negative")
