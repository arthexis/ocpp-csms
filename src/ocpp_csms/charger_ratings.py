"""Operator-owned nominal power ratings (not live meter values or charge limits).

Stored outside the operational SQLite schema so ratings can be changed without
migrating the authoritative charging database or sending OCPP control frames.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile

FILENAME = "charger-ratings.json"


def _positive_watts(kw: float) -> int:
    if isinstance(kw, bool) or not math.isfinite(kw) or kw <= 0:
        raise ValueError("Power rating must be a positive finite kW value")
    watts = kw * 1000
    if watts > 10_000_000 or round(watts) != watts:
        raise ValueError("Power rating must be at most 10000 kW, in whole watts")
    return int(watts)


def load_ratings(data_dir: str | Path) -> dict[str, dict]:
    path = Path(data_dir).expanduser() / FILENAME
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise ValueError("Unsafe charger ratings file")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Charger ratings must be a JSON object")
    for cp, config in data.items():
        if not isinstance(cp, str) or not cp or not isinstance(config, dict):
            raise ValueError("Invalid charger rating entry")
        if set(config) - {"default_w", "connectors"}:
            raise ValueError("Unknown charger rating field")
        default = config.get("default_w")
        if default is not None and (type(default) is not int or not 0 < default <= 10_000_000):
            raise ValueError("Invalid charger default rating")
        connectors = config.get("connectors", {})
        if not isinstance(connectors, dict):
            raise ValueError("Invalid connector rating mapping")
        for ident, watts in connectors.items():
            if not ident.isdecimal() or int(ident) <= 0 or str(int(ident)) != ident or (
                type(watts) is not int or not 0 < watts <= 10_000_000
            ):
                raise ValueError("Invalid connector nominal power rating")
    return data


def save_ratings(data_dir: str | Path, ratings: dict[str, dict]) -> None:
    root = Path(data_dir).expanduser()
    if not root.is_dir():
        raise ValueError("CSMS data directory does not exist")
    path = root / FILENAME
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Unsafe charger ratings file")
    fd, temporary = tempfile.mkstemp(prefix=".charger-ratings-", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(ratings, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def rating_for(ratings: dict[str, dict], cp: str, connector: int) -> tuple[int | None, str | None]:
    """Connector 0 means a charge-point-wide rating, never an outlet."""
    config = ratings.get(cp, {})
    if connector > 0 and str(connector) in config.get("connectors", {}):
        return int(config["connectors"][str(connector)]), "connector"
    if config.get("default_w") is not None:
        return int(config["default_w"]), "charger"
    return None, None


def attach_ratings(status: dict, data_dir: str | Path) -> None:
    """Enrich exported operational status without inventing physical outlets."""
    ratings = load_ratings(data_dir)
    for charger in status.get("chargers", []):
        cp = charger.get("id")
        if not isinstance(cp, str):
            continue
        config = ratings.get(cp, {})
        charger["rated_power_w"] = config.get("default_w")
        for connector in charger.get("connectors", []):
            ident = connector.get("id")
            if type(ident) is not int:
                continue
            watts, scope = rating_for(ratings, cp, ident)
            connector["rated_power_w"] = watts
            connector["rating_scope"] = scope
