import json

import pytest

from ocpp_discover import capture, discover


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
GRATUITOUS = (
    "12:00:00.000001 aa:bb:cc:dd:ee:ff > ff:ff:ff:ff:ff:ff, "
    "ethertype ARP (0x0806), length 42: ARP, Request who-has 192.168.129.182 "
    "tell 192.168.129.182, length 28\n"
    "12:00:01.000001 aa:bb:cc:dd:ee:ff > ff:ff:ff:ff:ff:ff, "
    "ethertype ARP (0x0806), length 42: ARP, Request who-has 192.168.129.182 "
    "tell 192.168.129.182, length 28\n"
)


def candidate():
    return discover.DiscoveryCandidate("eth0", "aa:bb:cc:dd:ee:ff", "192.168.129.182", "192.168.129.1", 2)


def test_discover_candidate_finds_one_repeated_unanswered_target():
    assert discover.discover_candidate(REQUEST + SECOND_REQUEST) == candidate()


def test_discover_candidate_ignores_answered_target():
    with pytest.raises(ValueError, match="no_unresolved_arp_candidate"):
        discover.discover_candidate(REQUEST + SECOND_REQUEST + REPLY)


def test_discover_candidate_requires_repetition_by_default():
    with pytest.raises(ValueError, match="no_unresolved_arp_candidate"):
        discover.discover_candidate(REQUEST)


def test_discover_candidate_rejects_ambiguous_targets():
    other = (REQUEST + SECOND_REQUEST).replace("192.168.129.1 ", "192.168.129.2 ")
    with pytest.raises(ValueError, match="ambiguous_arp_candidates"):
        discover.discover_candidate(REQUEST + SECOND_REQUEST + other)


def test_discover_candidate_rejects_multiple_requesters():
    other = (REQUEST + SECOND_REQUEST).replace("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:01")
    with pytest.raises(ValueError, match="ambiguous_arp_candidates"):
        discover.discover_candidate(REQUEST + SECOND_REQUEST + other)


def test_discover_candidate_ignores_gratuitous_arp():
    with pytest.raises(ValueError, match="no_unresolved_arp_candidate"):
        discover.discover_candidate(GRATUITOUS)


def test_discover_candidate_allows_explicit_interface_and_threshold():
    found = discover.discover_candidate(REQUEST, interface="eno1", min_requests=1)
    assert found.interface == "eno1"
    assert found.requests == 1


def test_discovery_rejects_invalid_interface_names():
    with pytest.raises(ValueError, match="invalid_interface"):
        discover.discover_candidate(REQUEST + SECOND_REQUEST, interface='eth0";flush')


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

    monkeypatch.setattr(capture.shutil, "which", lambda command: "/usr/bin/tcpdump")
    monkeypatch.setattr(capture.subprocess, "Popen", popen)

    assert discover.capture_arp("eth0", 3) == REQUEST + SECOND_REQUEST
    assert calls["command"] == ["tcpdump", "-i", "eth0", "-l", "-nn", "-e", "arp"]
    assert calls["timeouts"][0] == 3
    assert calls["terminated"] is True
    assert "killed" not in calls


def test_capture_arp_reports_missing_tcpdump(monkeypatch):
    monkeypatch.setattr(capture.shutil, "which", lambda command: None)
    with pytest.raises(RuntimeError, match="tcpdump_not_found"):
        discover.capture_arp("eth0", 3)


def test_cli_defaults_to_eth0_and_prints_candidate(monkeypatch, capsys):
    seen = {}

    def fake_discover(*, interface, seconds, min_requests):
        seen.update(interface=interface, seconds=seconds, min_requests=min_requests)
        return candidate()

    monkeypatch.setattr(discover, "discover", fake_discover)

    assert discover.main([]) == 0
    assert seen == {"interface": "eth0", "seconds": 15.0, "min_requests": 2}
    assert '"target_ip": "192.168.129.1"' in capsys.readouterr().out


def test_interface_addresses_reads_only_ipv4_on_requested_interface(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = json.dumps([{"addr_info": [{"family": "inet", "local": "10.0.0.5"}, {"family": "inet6", "local": "fe80::1"}]}])

    monkeypatch.setattr(discover, "_run_ip", lambda command: calls.append(command) or Result())

    assert discover.interface_addresses("eth9") == {"10.0.0.5"}
    assert calls == [["ip", "-j", "address", "show", "dev", "eth9"]]


def test_host_addresses_reads_ipv4_across_all_interfaces(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = json.dumps([
            {"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.129.10"}]},
            {"ifname": "wlan0", "addr_info": [{"family": "inet", "local": "10.42.0.1"}, {"family": "inet6", "local": "fe80::1"}]},
        ])

    monkeypatch.setattr(discover, "_run_ip", lambda command: calls.append(command) or Result())

    assert discover.host_addresses() == {"192.168.129.10", "10.42.0.1"}
    assert calls == [["ip", "-j", "address", "show"]]


def test_claim_address_requires_root_and_refuses_preexisting_address(tmp_path, monkeypatch):
    monkeypatch.setattr(discover.os, "geteuid", lambda: 1000)
    with pytest.raises(RuntimeError, match="root_required"):
        discover.claim_address(candidate(), tmp_path)

    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    monkeypatch.setattr(discover, "interface_addresses", lambda interface: {"192.168.129.1"})
    with pytest.raises(RuntimeError, match="target_address_already_present"):
        discover.claim_address(candidate(), tmp_path)
    assert not (tmp_path / "address.json").exists()


def test_claim_address_records_ownership_before_exact_ip_add(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    monkeypatch.setattr(discover, "interface_addresses", lambda interface: {"10.0.0.5"})

    class Result:
        returncode = 0
        stderr = ""

    def run_ip(command):
        calls.append(command)
        assert (tmp_path / "address.json").exists()
        return Result()

    monkeypatch.setattr(discover, "_run_ip", run_ip)

    claim = discover.claim_address(candidate(), tmp_path)
    assert claim == discover.AddressClaim("eth0", "192.168.129.1")
    assert calls == [["ip", "address", "add", "192.168.129.1/32", "dev", "eth0"]]
    assert json.loads((tmp_path / "address.json").read_text()) == {"address": "192.168.129.1", "interface": "eth0"}


def test_claim_address_removes_receipt_when_ip_add_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    monkeypatch.setattr(discover, "interface_addresses", lambda interface: set())

    class Result:
        returncode = 2
        stderr = "RTNETLINK answers: File exists"

    monkeypatch.setattr(discover, "_run_ip", lambda command: Result())

    with pytest.raises(RuntimeError, match="File exists"):
        discover.claim_address(candidate(), tmp_path)
    assert not (tmp_path / "address.json").exists()


def test_claim_address_refuses_existing_ownership_receipt(tmp_path, monkeypatch):
    (tmp_path / "address.json").write_text('{"interface":"eth0","address":"192.168.129.1"}')
    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    with pytest.raises(RuntimeError, match="address_claim_exists"):
        discover.claim_address(candidate(), tmp_path)


def test_cleanup_removes_only_owned_address_and_receipt(tmp_path, monkeypatch):
    (tmp_path / "address.json").write_text('{"interface":"eth0","address":"192.168.129.1"}')
    calls = []
    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    monkeypatch.setattr(discover, "interface_addresses", lambda interface: {"10.0.0.5", "172.16.0.9", "192.168.129.1"})

    class Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(discover, "_run_ip", lambda command: calls.append(command) or Result())

    assert discover.cleanup_address(tmp_path) == discover.AddressClaim("eth0", "192.168.129.1")
    assert calls == [["ip", "address", "del", "192.168.129.1/32", "dev", "eth0"]]
    assert not (tmp_path / "address.json").exists()


def test_cleanup_is_safe_when_owned_address_is_already_absent(tmp_path, monkeypatch):
    (tmp_path / "address.json").write_text('{"interface":"eth0","address":"192.168.129.1"}')
    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    monkeypatch.setattr(discover, "interface_addresses", lambda interface: {"10.0.0.5"})
    monkeypatch.setattr(discover, "_run_ip", lambda command: pytest.fail("no delete should run"))

    discover.cleanup_address(tmp_path)
    assert not (tmp_path / "address.json").exists()
