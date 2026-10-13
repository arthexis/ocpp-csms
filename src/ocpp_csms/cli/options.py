"""Shared CLI argument definitions; parsing behavior remains command-owned."""
from __future__ import annotations

import argparse


def add_cp(parser: argparse.ArgumentParser, **kwargs):
    """Register the charge-point selector with a stable namespace destination."""
    return parser.add_argument("--cp", dest="charger", **kwargs)


def add_connector(parser: argparse.ArgumentParser, **kwargs):
    """Register the connector selector (distinct from charge-point ID)."""
    return parser.add_argument("-c", "--connector", type=int, **kwargs)


def add_json(parser: argparse.ArgumentParser, *, help: str = "Print JSON output"):
    return parser.add_argument("-j", "--json", action="store_true", help=help)


def add_limit(parser: argparse.ArgumentParser, **kwargs):
    return parser.add_argument("-n", "--limit", type=int, **kwargs)


def add_time_bounds(parser: argparse.ArgumentParser, *, since_help: str = "Lower timestamp bound", until_help: str = "Upper timestamp bound"):
    parser.add_argument("--since", help=since_help)
    parser.add_argument("--until", help=until_help)


def require_nonnegative(value: int | None, *, name: str) -> None:
    """Validate optional nonnegative numeric selectors consistently."""
    if value is not None and value < 0:
        raise ValueError(f"{name} must be zero or greater")


def require_positive(value: int | None, *, name: str) -> None:
    """Validate positive identifiers and limits consistently."""
    if value is None or value < 1:
        raise ValueError(f"{name} must be at least 1")


def validate_time_order(since, until) -> None:
    """Reject reversed time bounds; callers resolve timestamps first."""
    if since is not None and until is not None and since > until:
        raise ValueError("lower time bound must not be after upper time bound")
