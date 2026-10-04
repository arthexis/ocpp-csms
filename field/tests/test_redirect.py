import json

import pytest

from field import redirect


UPGRADE_ONE = """12:00:00.000001 IP 192.168.129.182.40200 > 203.0.113.10.80: Flags [P.], length 180
GET /services/ocppj/CHARGER HTTP/1.1
Host: cloud.example
Upgrade: websocket
Connection: keep-alive, Upgrade

"""

UPGRADE_TWO = """12:00:01.000001 IP 192.168.129.182.40201 > 203.0.113.11.80: Flags [P.], length 170
GET /ocpp-j/CHARGER HTTP/1.1
Host: backup.example
Upgrade: websocket
Connection: Upgrade

"""


def test_parse_capture_builds_receipt_for_one_source_and_multiple_destinations():
    receipt = redirect.parse_capture(
        UPGRADE_ONE + UPGRADE_TWO,
        interface="eth0",
        listen_port=9000,
        captured_at="2026-10-04T04:00:00+00:00",
    )

    assert receipt.source_ip == "192.168.129.182"
    assert receipt.destination_ips == ["203.0.113.10", "203.0.113.11"]
    assert [(request.host, request.path) for request in receipt.requests] == [
        ("cloud.example", "/services/ocppj/CHARGER"),
        ("backup.example", "/ocpp-j/CHARGER"),
    ]
    assert receipt.interface == "eth0"
    assert receipt.listen_port == 9000


def test_parse_capture_deduplicates_repeated_upgrade():
    receipt = redirect.parse_capture(UPGRADE_ONE + UPGRADE_ONE, interface="eth0", listen_port=9000)

    assert receipt.destination_ips == ["203.0.113.10"]
    assert len(receipt.requests) == 1


def test_parse_capture_rejects_ambiguous_sources():
    other = UPGRADE_TWO.replace("192.168.129.182", "192.168.129.183")

    with pytest.raises(ValueError, match="ambiguous_websocket_sources"):
        redirect.parse_capture(UPGRADE_ONE + other, interface="eth0", listen_port=9000)


def test_parse_capture_rejects_tls_or_opaque_secure_traffic():
    capture = "12:00:00.000001 IP 192.168.129.182.40200 > 203.0.113.10.443: Flags [P.], length 120\n....binary....\n"

    with pytest.raises(ValueError, match="secure_or_opaque_traffic"):
        redirect.parse_capture(capture, interface="eth0", listen_port=9000)


def test_parse_capture_rejects_plain_http_without_websocket_upgrade():
    capture = """12:00:00.000001 IP 192.168.129.182.40200 > 203.0.113.10.80: Flags [P.], length 80
GET /health HTTP/1.1
Host: cloud.example
Connection: keep-alive

"""

    with pytest.raises(ValueError, match="no_plaintext_websocket_upgrade"):
        redirect.parse_capture(capture, interface="eth0", listen_port=9000)


def test_capture_writes_receipt_only_after_listener_and_valid_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(redirect, "listener_available", lambda port: port == 9000)
    monkeypatch.setattr(redirect, "capture_text", lambda interface, seconds: UPGRADE_ONE)

    receipt = redirect.capture("eth0", 9000, 10, tmp_path)
    saved = json.loads((tmp_path / "redirect.json").read_text())

    assert receipt.source_ip == "192.168.129.182"
    assert saved["source_ip"] == "192.168.129.182"
    assert saved["destination_ips"] == ["203.0.113.10"]
    assert saved["requests"][0]["path"] == "/services/ocppj/CHARGER"


def test_capture_refuses_when_listener_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(redirect, "listener_available", lambda port: False)

    with pytest.raises(RuntimeError, match="listener_unavailable"):
        redirect.capture("eth0", 9000, 10, tmp_path)

    assert not (tmp_path / "redirect.json").exists()


def test_capture_refuses_to_overwrite_existing_receipt(tmp_path, monkeypatch):
    (tmp_path / "redirect.json").write_text("existing")
    monkeypatch.setattr(redirect, "listener_available", lambda port: True)

    with pytest.raises(RuntimeError, match="redirect_receipt_exists"):
        redirect.capture("eth0", 9000, 10, tmp_path)


def test_capture_text_invokes_bounded_tcpdump(monkeypatch):
    calls = {}

    class Process:
        returncode = None

        def communicate(self, timeout=None):
            calls.setdefault("timeouts", []).append(timeout)
            if len(calls["timeouts"]) == 1:
                raise redirect.subprocess.TimeoutExpired("tcpdump", timeout)
            self.returncode = -15
            return UPGRADE_ONE, ""

        def terminate(self):
            calls["terminated"] = True

        def kill(self):
            calls["killed"] = True

    def popen(command, **kwargs):
        calls["command"] = command
        return Process()

    monkeypatch.setattr(redirect.shutil, "which", lambda command: "/usr/bin/tcpdump")
    monkeypatch.setattr(redirect.subprocess, "Popen", popen)

    assert redirect.capture_text("eth9", 3) == UPGRADE_ONE
    assert calls["command"][-1] == "tcp dst port 80 or tcp dst port 443"
    assert calls["command"][2] == "eth9"
    assert calls["timeouts"][0] == 3
    assert calls["terminated"] is True
    assert "killed" not in calls
