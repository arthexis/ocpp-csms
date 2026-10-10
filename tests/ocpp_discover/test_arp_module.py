"""ARP parser extraction retains its legacy import and validation behavior."""

import pytest

from ocpp_discover import discover
from ocpp_discover.arp import discover_candidate


def test_arp_parser_public_import_identity():
    assert discover.discover_candidate is discover_candidate


def test_arp_parser_rejects_invalid_interface_before_parsing():
    with pytest.raises(ValueError, match="invalid_interface"):
        discover_candidate("", interface="eth0;echo bad")


def test_arp_parser_rejects_nonpositive_request_threshold():
    with pytest.raises(ValueError, match="min_requests_must_be_positive"):
        discover_candidate("", min_requests=0)
