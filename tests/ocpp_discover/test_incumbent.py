from types import SimpleNamespace

import pytest

from ocpp_discover import incumbent


HANDSHAKE = """12:00:00.000000 IP 192.168.50.20.50000 > 192.168.50.1.8888: Flags [P.], seq 1:100
GET /ocpp/CP1 HTTP/1.1
Host: 192.168.50.1:8888
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Protocol: ocpp1.6

"""

RESULT = """12:00:01.000000 IP 192.168.50.1.8888 > 192.168.50.20.50000: Flags [P.], seq 1:40
..[3,"abc123",{"currentTime":"2026-10-07T03:00:00Z"}]
"""


def test_observed_endpoint_accepts_ocpp_websocket_handshake():
    assert incumbent._observed_endpoints(HANDSHAKE, {"192.168.50.1"}) == {
        (8888, "192.168.50.20", "websocket_subprotocol")
    }


def test_observed_endpoint_accepts_unmasked_server_ocpp_frame():
    assert incumbent._observed_endpoints(RESULT, {"192.168.50.1"}) == {
        (8888, "192.168.50.20", "ocpp_frame")
    }


def test_observe_endpoint_rejects_multiple_local_ocpp_endpoints(monkeypatch):
    capture = HANDSHAKE + HANDSHAKE.replace(".8888", ".9001").replace(":8888", ":9001")
    monkeypatch.setattr(incumbent.discover, "host_addresses", lambda: {"192.168.50.1"})
    monkeypatch.setattr(incumbent.discover, "capture_passive_tcp", lambda *args, **kwargs: capture)
    with pytest.raises(RuntimeError, match="ambiguous_local_ocpp_endpoints"):
        incumbent.observe_endpoint("eth0", 1)


def test_resolve_incumbent_maps_observed_port_to_systemd_service(monkeypatch):
    monkeypatch.setattr(incumbent.os, "geteuid", lambda: 0)
    monkeypatch.setattr(incumbent, "observe_endpoint", lambda *args, **kwargs: (8888, "192.168.50.20", "ocpp_frame"))
    monkeypatch.setattr(incumbent, "listener_pids", lambda port: [689])
    monkeypatch.setattr(incumbent, "service_for_pid", lambda pid: "another-csms.service")
    monkeypatch.setattr(incumbent.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    endpoint = incumbent.resolve_incumbent("eth0", 1, "ocpp-csms.service")

    assert endpoint is not None
    assert endpoint.service == "another-csms.service"
    assert endpoint.port == 8888
    assert endpoint.pid == 689


def test_managed_service_is_not_an_incumbent(monkeypatch):
    monkeypatch.setattr(incumbent.os, "geteuid", lambda: 0)
    monkeypatch.setattr(incumbent, "observe_endpoint", lambda *args, **kwargs: (9000, "192.168.50.20", "ocpp_frame"))
    monkeypatch.setattr(incumbent, "listener_pids", lambda port: [777])
    monkeypatch.setattr(incumbent, "service_for_pid", lambda pid: "ocpp-csms.service")

    assert incumbent.resolve_incumbent("eth0", 1, "ocpp-csms.service") is None



def test_resolve_incumbent_falls_back_to_established_socket_owner(monkeypatch):
    monkeypatch.setattr(incumbent.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        incumbent,
        "observe_endpoint",
        lambda *args, **kwargs: (8888, "192.168.50.20", "ocpp_frame"),
    )
    monkeypatch.setattr(incumbent, "listener_pids", lambda port: [])
    monkeypatch.setattr(
        incumbent,
        "established_socket_owners",
        lambda peer_ip: [(9001, 689)],
    )
    monkeypatch.setattr(incumbent, "service_for_pid", lambda pid: "another-csms.service")
    monkeypatch.setattr(
        incumbent.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    endpoint = incumbent.resolve_incumbent("eth0", 1, "ocpp-csms.service")

    assert endpoint is not None
    assert endpoint.service == "another-csms.service"
    assert endpoint.port == 9001
    assert endpoint.pid == 689


def test_resolve_incumbent_rejects_traffic_without_socket_owner(monkeypatch):
    monkeypatch.setattr(incumbent.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        incumbent,
        "observe_endpoint",
        lambda *args, **kwargs: (8888, "192.168.50.20", "ocpp_frame"),
    )
    monkeypatch.setattr(incumbent, "listener_pids", lambda port: [])
    monkeypatch.setattr(incumbent, "established_socket_owners", lambda peer_ip: [])

    with pytest.raises(RuntimeError, match="observed_ocpp_endpoint_has_no_local_socket_owner"):
        incumbent.resolve_incumbent("eth0", 1, "ocpp-csms.service")



def test_established_socket_owners_matches_peer_and_recovers_local_port(monkeypatch):
    stdout = (
        '0 0 192.168.50.1:9001 192.168.50.20:50000 '
        'users:(("python3",pid=689,fd=9))\n'
        '0 0 192.168.50.1:22 192.168.50.99:51000 '
        'users:(("sshd",pid=42,fd=4))\n'
    )
    monkeypatch.setattr(
        incumbent.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    )

    assert incumbent.established_socket_owners("192.168.50.20") == [(9001, 689)]


def test_resolve_incumbent_rejects_multiple_local_ports_for_same_peer(monkeypatch):
    monkeypatch.setattr(incumbent.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        incumbent,
        "observe_endpoint",
        lambda *args, **kwargs: (8888, "192.168.50.20", "ocpp_frame"),
    )
    monkeypatch.setattr(incumbent, "listener_pids", lambda port: [])
    monkeypatch.setattr(
        incumbent,
        "established_socket_owners",
        lambda peer_ip: [(9001, 689), (9002, 690)],
    )

    with pytest.raises(RuntimeError, match="ambiguous_incumbent_local_ports"):
        incumbent.resolve_incumbent("eth0", 1, "ocpp-csms.service")
