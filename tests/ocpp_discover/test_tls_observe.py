"""Passive TLS observation is evidence only, never a WSS assertion."""
import ipaddress
import struct

from ocpp_discover import tls_observe


def vector(data, width):
    return len(data).to_bytes(width, "big") + data


def hello(sni=b"charger.example.test", *, versions=True):
    host = b"\x00" + vector(sni, 2)
    sni_ext = b"\x00\x00" + vector(vector(host, 2), 2)
    alpn_ext = b"\x00\x10" + vector(vector(vector(b"h2", 1), 2), 2)
    version_ext = b"\x00\x2b" + vector(vector(b"\x03\x04\x03\x03", 1), 2) if versions else b""
    extensions = sni_ext + alpn_ext + version_ext
    body = b"\x03\x03" + bytes(32) + vector(b"", 1) + vector(b"\x13\x01", 2) + vector(b"\x00", 1) + vector(extensions, 2)
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x03" + vector(handshake, 2)


def pcap(chunks, *, reorder=False):
    raw = bytearray(b"\xd4\xc3\xb2\xa1" + struct.pack("<HHIIII", 2, 4, 0, 0, 65535, 1))
    segments = []
    seq = 100
    for chunk in chunks:
        ip = bytearray(20)
        ip[0] = 0x45
        struct.pack_into("!H", ip, 2, 20 + 20 + len(chunk))
        ip[9] = 6
        ip[12:16] = ipaddress.IPv4Address("10.0.0.50").packed
        ip[16:20] = ipaddress.IPv4Address("10.0.0.1").packed
        tcp = bytearray(20)
        struct.pack_into("!HHI", tcp, 0, 50000, 443, seq)
        tcp[12] = 0x50
        frame = bytes.fromhex("00112233445566778899aabb0800") + ip + tcp + chunk
        segments.append(frame)
        seq += len(chunk)
    if reorder:
        segments.reverse()
    for frame in segments:
        raw.extend(struct.pack("<IIII", 0, 0, len(frame), len(frame)) + frame)
    return bytes(raw)


def test_client_hello_extracts_sni_alpn_versions():
    candidates = tls_observe.observe_pcap(pcap([hello()]))
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_ip == "10.0.0.50"
    assert candidate.destination_ip == "10.0.0.1"
    assert candidate.destination_port == 443
    assert candidate.server_name == "charger.example.test"
    assert candidate.alpn == ("h2",)
    assert candidate.offered_versions == ("0x0304", "0x0303")
    assert candidate.classification == "tls_candidate"


def test_fragmented_out_of_order_tcp_is_reassembled():
    data = hello()
    assert len(tls_observe.observe_pcap(pcap([data[:17], data[17:50], data[50:]], reorder=True))) == 1


def test_fragmented_tls_record_handshake():
    data = hello()
    handshake = data[5:]
    first = b"\x16\x03\x03" + vector(handshake[:10], 2)
    second = b"\x16\x03\x03" + vector(handshake[10:], 2)
    assert len(tls_observe.observe_pcap(pcap([first, second]))) == 1


def test_not_tls_not_discovered():
    assert tls_observe.observe_pcap(pcap([b"GET / HTTP/1.1\\r\\n"])) == []


def test_incomplete_hello_not_discovered():
    assert tls_observe.observe_pcap(pcap([hello()[:25]])) == []
