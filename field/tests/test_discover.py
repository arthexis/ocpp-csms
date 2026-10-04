import pytest

from field import discover


REQUEST = (
    "12:00:00.000001 aa:bb:cc:dd:ee:ff > ff:ff:ff:ff:ff:ff, "
    "ethertype ARP (0x0806), length 42: ARP, Request who-has 192.168.129.1 "
    "tell 192.168.129.182, length 28\n"
)
SECOND_REQUEST = REQUEST.replace("12:00:00.000001", "12:00:01.000001")
REPLY = (
    "12:00:01.500000 11:22:33:44:55:66 > aa:bb:cc:dd:ee:ff, "
    "ethertype ARP (0x0806), length 42: ARP, Reply 192.168.129.1 "
    "is-at 11:22:33:44:55:66, length 28\n"
)


def test_discover_candidate_finds_one_repeated_unanswered_target():
    candidate = discover.discover_candidate(REQUEST + SECOND_REQUEST)

    assert candidate.interface == "eth0"
    assert candidate.source_mac == "aa:bb:cc:dd:ee:ff"
    assert candidate.source_ip == "192.168.129.182"
    assert candidate.target_ip == "192.168.129.1"
    assert candidate.requests == 2


def test_discover_candidate_ignores_answered_target():
    with pytest.raises(ValueError, match="no_unresolved_arp_candidate"):
        discover.discover_candidate(REQUEST + SECOND_REQUEST + REPLY)


def test_discover_candidate_requires_repetition_by_default():
    with pytest.raises(ValueError, match="no_unresolved_arp_candidate"):
        discover.discover_candidate(REQUEST)


def test_discover_candidate_rejects_ambiguous_targets():
    other = (REQUEST + SECOND_REQUEST).replace("192.168.129.1", "192.168.129.2")

    with pytest.raises(ValueError, match="ambiguous_arp_candidates"):
        discover.discover_candidate(REQUEST + SECOND_REQUEST + other)


def test_discover_candidate_rejects_multiple_requesters():
    other = (REQUEST + SECOND_REQUEST).replace("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:01")

    with pytest.raises(ValueError, match="ambiguous_arp_candidates"):
        discover.discover_candidate(REQUEST + SECOND_REQUEST + other)


def test_discover_candidate_ignores_gratuitous_arp():
    gratuitous = (REQUEST + SECOND_REQUEST).replace("192.168.129.1", "192.168.129.182")

    with pytest.raises(ValueError, match="no_unresolved_arp_candidate"):
        discover.discover_candidate(gratuitous)


def test_discover_candidate_allows_explicit_interface_and_threshold():
    candidate = discover.discover_candidate(
        REQUEST,
        interface="eno1",
        min_requests=1,
    )

    assert candidate.interface == "eno1"
    assert candidate.requests == 1


def test_capture_arp_uses_default_bounded_tcpdump_shape(monkeypatch):
    calls = {}

    class Process:
        returncode = None

        def communicate(self, timeout=None):
            calls.setdefault("timeouts", []).append(timeout)
            if len(calls["timeouts"]) == 1:
                raise discover.subprocess.TimeoutExpired("tcpdump", timeout)
            self.returncode = -15
            return REQUEST + SECOND_REQUEST, ""

        def terminate(self):
            calls["terminated"] = True

        def kill(self):
            calls["killed"] = True

    def popen(command, **kwargs):
        calls["command"] = command
        return Process()

    monkeypatch.setattr(discover.shutil, "which", lambda command: "/usr/bin/tcpdump")
    monkeypatch.setattr(discover.subprocess, "Popen", popen)

    text = discover.capture_arp("eth0", 3)

    assert text == REQUEST + SECOND_REQUEST
    assert calls["command"] == ["tcpdump", "-i", "eth0", "-l", "-nn", "-e", "arp"]
    assert calls["timeouts"][0] == 3
    assert calls["terminated"] is True
    assert "killed" not in calls


def test_capture_arp_reports_missing_tcpdump(monkeypatch):
    monkeypatch.setattr(discover.shutil, "which", lambda command: None)

    with pytest.raises(RuntimeError, match="tcpdump_not_found"):
        discover.capture_arp("eth0", 3)


def test_cli_defaults_to_eth0_and_prints_candidate(monkeypatch, capsys):
    seen = {}

    def fake_discover(*, interface, seconds, min_requests):
        seen.update(interface=interface, seconds=seconds, min_requests=min_requests)
        return discover.DiscoveryCandidate(interface, "aa:bb:cc:dd:ee:ff", "192.168.129.182", "192.168.129.1", 2)

    monkeypatch.setattr(discover, "discover", fake_discover)

    assert discover.main([]) == 0
    output = capsys.readouterr().out
    assert seen == {"interface": "eth0", "seconds": 15.0, "min_requests": 2}
    assert '"target_ip": "192.168.129.1"' in output
