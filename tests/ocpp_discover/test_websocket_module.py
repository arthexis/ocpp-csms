"""Extracted WebSocket parser preserves the original discovery imports."""

from ocpp_discover import discover
from ocpp_discover import websocket


def test_websocket_parsers_remain_importable_from_discover():
    assert discover.parse_tcp_websocket is websocket.parse_tcp_websocket
    assert discover.parse_passive_websocket is websocket.parse_passive_websocket
    assert discover._websocket_receipt is websocket._websocket_receipt
    assert discover._validate_candidate is websocket._validate_candidate


def test_passive_parser_rejects_missing_local_addresses():
    try:
        websocket.parse_passive_websocket("", interface="eth0", local_addresses=set(), listen_port=9000)
    except ValueError as exc:
        assert str(exc) == "no_local_ipv4_addresses"
    else:
        raise AssertionError("missing local addresses must be rejected")
