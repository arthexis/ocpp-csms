"""The provisional SYN observer still uses the shared TCP packet grammar."""

from ocpp_discover import discover, websocket


def test_provisional_tcp_packet_pattern_is_shared():
    assert discover._TCP_PACKET is websocket._TCP_PACKET
    packet = "12:00:00.000000 IP 192.0.2.5.50000 > 192.0.2.1.8888: Flags [S], seq 1"
    match = discover._TCP_PACKET.search(packet)
    assert match is not None
    assert match.group("src") == "192.0.2.5"
    assert match.group("dst_port") == "8888"
