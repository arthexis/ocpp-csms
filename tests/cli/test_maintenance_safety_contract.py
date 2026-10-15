"""final regression checks for maintenance grammar and safety invariants."""
from __future__ import annotations


import pytest

from ocpp_csms.cli import build_parser
from ocpp_csms.cli.firmware import firmware_request
from ocpp_csms.cli.charger_diagnostics import _request as diagnostics_request
from ocpp_csms.cli.maintenance import maintenance_request


def parse(*words):
    parser, _ = build_parser()
    return parser.parse_args(words)


@pytest.mark.parametrize("words", [
    ("trigger", "heartbeat", "-c", "1"),
    ("trigger", "meter", "-c", "0"),
    ("unlock", "-c", "0"),
    ("avail", "disable", "-c", "-1"),
])
def test_unsafe_connector_selection_is_rejected(words):
    with pytest.raises(ValueError):
        maintenance_request(parse(*words))


def test_diagnostics_history_is_default_read_only_mode():
    args = parse("diagnose", "--cp", "CP1")
    assert args.location is None
    assert args.charger == "CP1"


def test_diagnostics_upload_requires_explicit_location():
    args = parse("diagnose")
    with pytest.raises(ValueError, match="--location is required"):
        diagnostics_request(args)


@pytest.mark.parametrize("words", [
    ("firmware", "update", "--location", "https://example.org/fw", "--confirm", "--retries", "-1"),
    ("firmware", "update", "--location", "https://example.org/fw", "--confirm", "--retry-interval", "-1"),
])
def test_firmware_negative_retries_rejected(words):
    with pytest.raises(ValueError, match="non-negative"):
        firmware_request(parse(*words))
