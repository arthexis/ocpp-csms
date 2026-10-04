import json

import pytest

from field import discover, redirect


CANDIDATE = discover.DiscoveryCandidate(
    "eth0",
    "aa:bb:cc:dd:ee:ff",
    "192.168.129.182",
    "192.168.129.1",
    2,
)


def packet(destination="203.0.113.10", port=80, path="/ocpp/CHARGER", host="cloud.example"):
    return f"""12:00:02.000001 IP 192.168.129.182.40200 > {destination}.{port}: Flags [P.], length 180
GET {path} HTTP/1.1
Host: {host}
Upgrade: websocket
Connection: keep-alive, Upgrade

"""


def test_parse_tcp_websocket_builds_port_aware_redirect_receipt():
    receipt = discover.parse_tcp_websocket(packet(port=8888), CANDIDATE, listen_port=9000)

    assert receipt.interface == "eth0"
    assert receipt.source_ip == "192.168.129.182"
    assert receipt.destination_ips == ["203.0.113.10"]
    assert receipt.destination_port == 8888
    assert receipt.listen_port == 9000
    assert receipt.requests == [redirect.WebSocketRequest("203.0.113.10", "cloud.example", "/ocpp/CHARGER")]


def test_parse_tcp_websocket_supports_direct_target_case():
    receipt = discover.parse_tcp_websocket(
        packet(destination="192.168.129.1", port=80),
        CANDIDATE,
        listen_port=9000,
    )

    assert receipt.destination_ips == ["192.168.129.1"]
    assert receipt.destination_port == 80


def test_parse_tcp_websocket_allows_multiple_destinations_on_one_port():
    text = packet("203.0.113.11", 8080, host="one.example") + packet(
        "203.0.113.10", 8080, host="two.example"
    )

    receipt = discover.parse_tcp_websocket(text, CANDIDATE, listen_port=9000)

    assert receipt.destination_ips == ["203.0.113.10", "203.0.113.11"]
    assert receipt.destination_port == 8080
    assert len(receipt.requests) == 2


def test_parse_tcp_websocket_refuses_multiple_candidate_ports():
    text = packet(port=80) + packet(port=8080)

    with pytest.raises(ValueError, match="ambiguous_tcp_destinations"):
        discover.parse_tcp_websocket(text, CANDIDATE, listen_port=9000)


def test_parse_tcp_websocket_refuses_tls_without_plaintext_evidence():
    text = "12:00:02.000001 IP 192.168.129.182.40200 > 203.0.113.10.443: Flags [S], length 0\n"

    with pytest.raises(ValueError, match="secure_or_opaque_traffic"):
        discover.parse_tcp_websocket(text, CANDIDATE, listen_port=9000)


def test_parse_tcp_websocket_refuses_non_websocket_tcp():
    text = """12:00:02.000001 IP 192.168.129.182.40200 > 203.0.113.10.8080: Flags [P.], length 50
GET /health HTTP/1.1
Host: cloud.example
Connection: keep-alive

"""

    with pytest.raises(ValueError, match="no_plaintext_websocket_upgrade"):
        discover.parse_tcp_websocket(text, CANDIDATE, listen_port=9000)


def test_parse_tcp_websocket_ignores_other_source_ips():
    text = packet().replace("192.168.129.182", "192.168.129.183")

    with pytest.raises(ValueError, match="no_plaintext_websocket_upgrade"):
        discover.parse_tcp_websocket(text, CANDIDATE, listen_port=9000)


def test_capture_tcp_filters_to_discovered_mac_ip_and_interface(monkeypatch):
    calls = {}

    def bounded(command, seconds):
        calls["command"] = command
        calls["seconds"] = seconds
        return packet(port=8888)

    monkeypatch.setattr(discover, "_bounded_tcpdump", bounded)

    assert discover.capture_tcp(CANDIDATE, 4) == packet(port=8888)
    assert calls == {
        "command": [
            "tcpdump",
            "-i",
            "eth0",
            "-l",
            "-nn",
            "-s0",
            "-A",
            "ether src aa:bb:cc:dd:ee:ff and ip src 192.168.129.182 and tcp",
        ],
        "seconds": 4,
    }


def test_capture_tcp_rejects_candidate_values_before_building_filter(monkeypatch):
    malicious = discover.DiscoveryCandidate(
        'eth0;rm',
        "aa:bb:cc:dd:ee:ff",
        "192.168.129.182",
        "192.168.129.1",
        2,
    )
    monkeypatch.setattr(discover, "_bounded_tcpdump", lambda *args: pytest.fail("capture must not run"))

    with pytest.raises(ValueError, match="invalid_interface"):
        discover.capture_tcp(malicious, 4)


def test_discover_tcp_combines_bounded_capture_and_parser(monkeypatch):
    monkeypatch.setattr(discover, "capture_tcp", lambda candidate, seconds: packet(port=8888))

    receipt = discover.discover_tcp(CANDIDATE, listen_port=9000, seconds=3)

    assert receipt.destination_port == 8888
    assert receipt.destination_ips == ["203.0.113.10"]


def test_redirect_ruleset_uses_discovered_destination_port():
    receipt = discover.parse_tcp_websocket(packet(port=8888), CANDIDATE, listen_port=9000)

    ruleset = redirect.render_ruleset(receipt)

    assert "tcp dport 8888 redirect to :9000" in ruleset
    assert "ip saddr 192.168.129.182" in ruleset
    assert "ip daddr { 203.0.113.10 }" in ruleset


def test_old_redirect_receipt_without_destination_port_loads_as_port_80(tmp_path):
    payload = {
        "interface": "eth0",
        "listen_port": 9000,
        "source_ip": "192.168.129.182",
        "destination_ips": ["203.0.113.10"],
        "requests": [
            {
                "destination_ip": "203.0.113.10",
                "host": "cloud.example",
                "path": "/ocpp/CHARGER",
            }
        ],
        "captured_at": "2026-10-04T04:00:00+00:00",
    }
    (tmp_path / "redirect.json").write_text(json.dumps(payload))

    assert redirect.load_receipt(tmp_path).destination_port == 80
