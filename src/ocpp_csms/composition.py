from __future__ import annotations

from ocpp_csms.server import CSMSServer


def build_server(host: str, port: int) -> CSMSServer:
    return CSMSServer(host=host, port=port)
