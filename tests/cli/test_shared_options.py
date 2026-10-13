"""Shared argument builders preserve existing parser namespaces and defaults."""
import argparse
import pytest
from ocpp_csms.cli.options import add_cp, add_connector, add_json, add_limit, add_time_bounds


def test_shared_selectors_and_json():
    parser = argparse.ArgumentParser()
    add_cp(parser)
    add_connector(parser)
    add_json(parser)
    args = parser.parse_args(["--cp", "CP1", "-c", "2", "-j"])
    assert (args.charger, args.connector, args.json) == ("CP1", 2, True)
    assert parser.parse_args(["--cp", "CP2", "--connector", "0"]).charger == "CP2"


def test_shared_time_and_limit():
    parser = argparse.ArgumentParser()
    add_time_bounds(parser)
    add_limit(parser, default=None)
    args = parser.parse_args(["--since", "7d", "--until", "1d", "-n", "20"])
    assert (args.since, args.until, args.limit) == ("7d", "1d", 20)


def test_canonical_only_cp_rejects_legacy_alias():
    parser = argparse.ArgumentParser()
    add_cp(parser)
    assert parser.parse_args(["--cp", "CP1"]).charger == "CP1"
    with pytest.raises(SystemExit):
        parser.parse_args(["--charger", "CP1"])
