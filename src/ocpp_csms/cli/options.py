"""Shared CLI argument definitions; parsing behavior remains command-owned."""
from __future__ import annotations

import argparse


def add_cp(parser: argparse.ArgumentParser, *, legacy_alias: bool = True, **kwargs):
    """Register the charge-point selector with a stable namespace destination."""
    flags = ("--cp", "--charger") if legacy_alias else ("--cp",)
    return parser.add_argument(*flags, dest="charger", **kwargs)


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
