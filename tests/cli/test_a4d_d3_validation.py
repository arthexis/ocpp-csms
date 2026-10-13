"""A4d D3: shared transfer validation preserves existing command error contracts."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ocpp_csms.cli.charger_diagnostics import _request
from ocpp_csms.cli.firmware import firmware_request
from ocpp_csms.cli.maintenance_validation import validate_retry_options, validate_transfer_url


@pytest.mark.parametrize("url", [
    "https://example.com/file.bin", "http://example.com/file.bin",
    "ftp://example.com/file.bin", "ftps://example.com/file.bin",
])
def test_supported_transfer_schemes(url):
    validate_transfer_url(url, error="bad URL")


@pytest.mark.parametrize("url", [
    "file:///tmp/firmware.bin", "https:///missing-host", "https://example.com/file#fragment",
    "not-a-url",
])
def test_invalid_transfer_urls_preserve_caller_error(url):
    with pytest.raises(ValueError, match="^invalid firmware URL$"):
        validate_transfer_url(url, error="invalid firmware URL")


@pytest.mark.parametrize(("retries", "interval", "error"), [
    (-1, None, "--retries must be non-negative"),
    (None, -1, "--retry-interval must be non-negative"),
])
def test_retry_errors(retries, interval, error):
    with pytest.raises(ValueError) as exc:
        validate_retry_options(retries, interval)
    assert str(exc.value) == error


def test_retry_zero_is_allowed():
    validate_retry_options(0, 0)


def test_firmware_request_preserves_validation_errors():
    args = SimpleNamespace(confirm=True, location="file:///tmp/firmware",
                           retries=None, retry_interval=None)
    with pytest.raises(ValueError, match="^invalid firmware URL$"):
        firmware_request(args)


def test_diagnostics_request_preserves_validation_errors():
    args = SimpleNamespace(location="file:///tmp/report", retries=None,
                           retry_interval=None)
    with pytest.raises(ValueError, match="--location must be an absolute"):
        _request(args)
