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


def receipt(**changes):
    values = {
        "interface": "eth0",
        "listen_port": 9000,
        "source_ip": "192.168.129.182",
        "destination_ips": ["203.0.113.11", "203.0.113.10"],
        "requests": [
            redirect.WebSocketRequest("203.0.113.10", "cloud.example", "/services/ocppj/CHARGER"),
            redirect.WebSocketRequest("203.0.113.11", "backup.example", "/ocpp-j/CHARGER"),
        ],
        "captured_at": "2026-10-04T04:00:00+00:00",
    }
    values.update(changes)
    return redirect.RedirectReceipt(**values)


def test_parse_capture_builds_receipt_for_one_source_and_multiple_destinations():
    parsed = redirect.parse_capture(
        UPGRADE_ONE + UPGRADE_TWO,
        interface="eth0",
        listen_port=9000,
        captured_at="2026-10-04T04:00:00+00:00",
    )

    assert parsed.source_ip == "192.168.129.182"
    assert parsed.destination_ips == ["203.0.113.10", "203.0.113.11"]
    assert [(request.host, request.path) for request in parsed.requests] == [
        ("cloud.example", "/services/ocppj/CHARGER"),
        ("backup.example", "/ocpp-j/CHARGER"),
    ]
    assert parsed.interface == "eth0"
    assert parsed.listen_port == 9000


def test_parse_capture_deduplicates_repeated_upgrade():
    parsed = redirect.parse_capture(UPGRADE_ONE + UPGRADE_ONE, interface="eth0", listen_port=9000)

    assert parsed.destination_ips == ["203.0.113.10"]
    assert len(parsed.requests) == 1


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

    parsed = redirect.capture("eth0", 9000, 10, tmp_path)
    saved = json.loads((tmp_path / "redirect.json").read_text())

    assert parsed.source_ip == "192.168.129.182"
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


def test_render_ruleset_is_narrow_and_deterministic():
    ruleset = redirect.render_ruleset(receipt())

    assert ruleset == """table ip ocpp_field_redirect {
  chain prerouting {
    type nat hook prerouting priority dstnat; policy accept;
    iifname "eth0" ip saddr 192.168.129.182 ip daddr { 203.0.113.10, 203.0.113.11 } tcp dport 80 redirect to :9000
  }
}
"""


def test_receipt_validation_rejects_values_that_could_widen_or_inject_rules():
    invalid = [
        (receipt(interface='eth0" counter'), "invalid_interface"),
        (receipt(source_ip="0.0.0.0/0"), "invalid_source_ip"),
        (receipt(destination_ips=[]), "no_destination_ips"),
        (receipt(destination_ips=["203.0.113.10", "0.0.0.0/0"]), "invalid_destination_ip"),
        (receipt(listen_port=70000), "invalid_listen_port"),
        (receipt(requests=[]), "no_websocket_requests"),
    ]

    for candidate, message in invalid:
        with pytest.raises(ValueError, match=message):
            redirect.render_ruleset(candidate)


def test_receipt_validation_requires_destination_set_to_match_capture_evidence_exactly():
    widened = receipt(destination_ips=["203.0.113.10", "203.0.113.11", "203.0.113.99"])
    missing = receipt(destination_ips=["203.0.113.10"])

    for candidate in (widened, missing):
        with pytest.raises(ValueError, match="destination_evidence_mismatch"):
            redirect.render_ruleset(candidate)


def test_load_receipt_round_trips_capture_evidence(tmp_path):
    candidate = receipt()
    (tmp_path / "redirect.json").write_text(json.dumps(candidate.to_json()))

    assert redirect.load_receipt(tmp_path) == candidate


def test_load_receipt_rejects_missing_invalid_or_wrong_shaped_evidence(tmp_path):
    with pytest.raises(RuntimeError, match="redirect_receipt_not_found"):
        redirect.load_receipt(tmp_path)

    (tmp_path / "redirect.json").write_text("not-json")
    with pytest.raises(ValueError, match="invalid_redirect_receipt"):
        redirect.load_receipt(tmp_path)

    payload = receipt().to_json()
    payload["destination_ips"] = "203.0.113.10"
    (tmp_path / "redirect.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="invalid_redirect_receipt"):
        redirect.load_receipt(tmp_path)


def test_validate_ruleset_uses_nft_check_without_applying(monkeypatch):
    calls = {}

    class Result:
        returncode = 0
        stderr = ""

    def run(command, **kwargs):
        calls["command"] = command
        calls["input"] = kwargs["input"]
        return Result()

    monkeypatch.setattr(redirect.shutil, "which", lambda command: "/usr/sbin/nft")
    monkeypatch.setattr(redirect.subprocess, "run", run)

    ruleset = redirect.validate_ruleset(receipt())

    assert calls["command"] == ["nft", "-c", "-f", "-"]
    assert calls["input"] == ruleset


def test_validate_ruleset_reports_missing_nft_and_validation_failure(monkeypatch):
    monkeypatch.setattr(redirect.shutil, "which", lambda command: None)
    with pytest.raises(RuntimeError, match="nft_not_found"):
        redirect.validate_ruleset(receipt())

    class Result:
        returncode = 1
        stderr = "stdin:4: syntax error"

    monkeypatch.setattr(redirect.shutil, "which", lambda command: "/usr/sbin/nft")
    monkeypatch.setattr(redirect.subprocess, "run", lambda *args, **kwargs: Result())
    with pytest.raises(RuntimeError, match="syntax error"):
        redirect.validate_ruleset(receipt())


def test_validate_cli_prints_checked_ruleset(tmp_path, monkeypatch, capsys):
    (tmp_path / "redirect.json").write_text(json.dumps(receipt().to_json()))
    monkeypatch.setattr(redirect, "validate_ruleset", redirect.render_ruleset)

    assert redirect.main(["validate", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "table ip ocpp_field_redirect" in output
    assert "192.168.129.182" in output
    assert "redirect to :9000" in output
