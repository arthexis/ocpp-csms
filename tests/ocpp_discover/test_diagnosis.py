from types import SimpleNamespace

import pytest

from ocpp_discover import diagnosis, persistence
from ocpp_discover.redirect import RedirectReceipt, WebSocketRequest


def receipt(*, port=8080, path="/ocpp/CP7"):
    return RedirectReceipt(
        interface="enp7s0",
        listen_port=9100,
        source_ip="172.16.5.40",
        destination_ips=["172.16.5.1"],
        requests=[WebSocketRequest("172.16.5.1", f"172.16.5.1:{port}", path)],
        captured_at="test",
        destination_port=port,
    )


class FakeProcess:
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.stderr = SimpleNamespace(read=lambda: "")
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_inspection_reports_persistent_and_live_configuration(tmp_path, monkeypatch):
    expected = receipt()
    ruleset = tmp_path / "nftables.conf"
    ruleset.write_text(persistence.render_persistent_ruleset(expected))
    monkeypatch.setattr(diagnosis.redirect, "table_exists", lambda: True)
    assert diagnosis.inspect_configuration(expected, ruleset_path=ruleset) == (True, True)


def test_inspection_reports_configuration_mismatch_without_mutation(tmp_path, monkeypatch):
    ruleset = tmp_path / "nftables.conf"
    ruleset.write_text("# different\n")
    monkeypatch.setattr(diagnosis.redirect, "table_exists", lambda: False)
    assert diagnosis.inspect_configuration(receipt(), ruleset_path=ruleset) == (False, False)


def test_passive_observation_ignores_absence(monkeypatch):
    monkeypatch.setattr(diagnosis.discover, "capture_passive_tcp", lambda interface, seconds: "")
    assert diagnosis.observe_contradiction(receipt(), seconds=1) is None


def test_passive_observation_returns_positive_endpoint_contradiction(monkeypatch):
    observed = receipt(port=9999)
    monkeypatch.setattr(diagnosis.discover, "capture_passive_tcp", lambda interface, seconds: "capture")
    monkeypatch.setattr(diagnosis.discover, "_websocket_receipt", lambda *args, **kwargs: observed)
    assert diagnosis.observe_contradiction(receipt(), seconds=1) == observed


def test_same_endpoint_is_not_a_contradiction(monkeypatch):
    expected = receipt()
    monkeypatch.setattr(diagnosis.discover, "capture_passive_tcp", lambda interface, seconds: "capture")
    monkeypatch.setattr(diagnosis.discover, "_websocket_receipt", lambda *args, **kwargs: expected)
    assert diagnosis.observe_contradiction(expected, seconds=1) is None


def _run_observer(monkeypatch, lines, *, expected=None):
    process = FakeProcess(lines)
    commands = []
    monkeypatch.setattr(diagnosis.shutil, "which", lambda name: "/usr/bin/tcpdump")
    monkeypatch.setattr(diagnosis.subprocess, "Popen", lambda command, **kwargs: commands.append(command) or process)
    evidence = diagnosis.wait_for_discovery_evidence("enp7s0", expected=expected)
    return evidence, commands[0], process


def test_passive_observer_uses_one_syn_as_immediate_evidence(monkeypatch):
    line = "12:00:00.000000 IP 172.16.5.40.50000 > 172.16.5.1.8080: Flags [S], seq 1\n"
    evidence, command, process = _run_observer(monkeypatch, [line], expected=receipt())
    assert command[-1] == "arp or (tcp[tcpflags] & (tcp-syn|tcp-ack) == tcp-syn)"
    assert evidence.kind == "expected"
    assert evidence.capture == line
    assert process.terminated


def test_single_syn_is_preserved_without_requiring_retry(monkeypatch):
    """The only TCP attempt may be the useful first-contact evidence."""
    line = "12:00:00.000000 IP 192.168.50.20.50000 > 192.168.50.1.8888: Flags [S], seq 1\n"
    evidence, _, process = _run_observer(monkeypatch, [line])
    assert evidence.kind == "candidate"
    assert evidence.capture == line
    assert process.terminated


def test_passive_observer_classifies_syn_from_replacement_charger(monkeypatch):
    line = "12:00:00.000000 IP 192.168.50.20.50000 > 192.168.50.1.8888: Flags [S], seq 1\n"
    evidence, _, _ = _run_observer(monkeypatch, [line], expected=receipt())
    assert evidence.kind == "candidate"


def test_single_arp_request_is_not_enough(monkeypatch):
    line = "12:00:00.000000 ARP, Request who-has 10.42.0.1 tell 10.42.0.50, length 28\n"
    with pytest.raises(RuntimeError, match="passive_observer_stopped"):
        _run_observer(monkeypatch, [line])


def test_passive_observer_requires_repeated_unanswered_arp(monkeypatch):
    first = "12:00:00.000000 ARP, Request who-has 10.42.0.1 tell 10.42.0.50, length 28\n"
    second = "12:00:01.000000 ARP, Request who-has 10.42.0.1 tell 10.42.0.50, length 28\n"
    evidence, command, _ = _run_observer(monkeypatch, [first, second], expected=receipt())
    assert "arp" in command[-1]
    assert evidence.kind == "candidate"
    assert evidence.capture == first + second


def test_passive_observer_ignores_self_arp_before_qualified_candidate(monkeypatch):
    self_arp = "12:00:00.000000 ARP, Request who-has 10.42.0.50 tell 10.42.0.50, length 28\n"
    first = "12:00:01.000000 ARP, Request who-has 10.42.0.1 tell 10.42.0.50, length 28\n"
    second = "12:00:02.000000 ARP, Request who-has 10.42.0.1 tell 10.42.0.50, length 28\n"
    evidence, _, _ = _run_observer(monkeypatch, [self_arp, first, second])
    assert evidence.capture == self_arp + first + second


def test_answered_arp_never_qualifies_even_after_more_requests(monkeypatch):
    request = "12:00:00.000000 ARP, Request who-has 10.42.0.1 tell 10.42.0.50, length 28\n"
    reply = "12:00:00.500000 ARP, Reply 10.42.0.1 is-at aa:bb:cc:dd:ee:ff, length 28\n"
    with pytest.raises(RuntimeError, match="passive_observer_stopped"):
        _run_observer(monkeypatch, [request, reply, request, request])


def test_passive_observer_does_not_accept_answered_arp_but_can_accept_other_target(monkeypatch):
    request = "12:00:00.000000 ARP, Request who-has 10.42.0.1 tell 10.42.0.50, length 28\n"
    reply = "12:00:00.500000 ARP, Reply 10.42.0.1 is-at aa:bb:cc:dd:ee:ff, length 28\n"
    other1 = "12:00:01.000000 ARP, Request who-has 10.42.0.2 tell 10.42.0.50, length 28\n"
    other2 = "12:00:02.000000 ARP, Request who-has 10.42.0.2 tell 10.42.0.50, length 28\n"
    evidence, _, _ = _run_observer(monkeypatch, [request, reply, request, other1, other2])
    assert evidence.capture.endswith(other2)


def test_tcp_ack_and_payload_are_excluded_before_python(monkeypatch):
    """The BPF expression, rather than Python parsing, excludes established traffic."""
    line = "12:00:00.000000 IP 172.16.5.40.50000 > 172.16.5.1.8080: Flags [S], seq 1\n"
    _, command, _ = _run_observer(monkeypatch, [line])
    packet_filter = command[-1]
    assert "tcp-syn|tcp-ack" in packet_filter
    assert "== tcp-syn" in packet_filter
    assert packet_filter != "tcp or arp"
