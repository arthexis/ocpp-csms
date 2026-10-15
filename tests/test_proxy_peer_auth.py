"""Only a real loopback TCP peer may use nginx-terminated Basic authentication."""
from http import HTTPStatus
from unittest.mock import patch

import pytest

from ocpp_csms.server import AuthenticatedWebSocketProtocol


@pytest.mark.asyncio
@pytest.mark.parametrize("peer,allowed", [
    (("127.0.0.1", 53000), True),
    (("::1", 53000, 0, 0), True),
    (("192.0.2.12", 53000), False),
    (("2001:db8::12", 53000, 0, 0), False),
])
async def test_proxy_auth_uses_socket_peer_not_forwarded_headers(peer, allowed):
    protocol = object.__new__(AuthenticatedWebSocketProtocol)
    protocol.auth = {"mode": "basic"}
    headers = {"X-Forwarded-Proto": "https", "X-Real-IP": "127.0.0.1"}
    with patch.object(AuthenticatedWebSocketProtocol, "remote_address", peer, create=True), \
         patch("ocpp_csms.server.basic_upgrade_check", return_value=None) as check:
        await protocol.process_request("/CP001", headers)
    assert check.call_args.kwargs["secure"] is allowed
    assert (check.call_args.kwargs["config_path"] is not None) is allowed
