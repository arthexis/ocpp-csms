from types import SimpleNamespace

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
    monkeypatch.setattr(
        diagnosis.subprocess,
        "Popen",
        lambda command, **kwargs: commands.append(command) or process,
    )
    evidence = diagnosis.wait_for_discovery_evidence("enp7s0", expected=expected)
    return evidence, commands[0], process


def test_passive_observer_uses_syn_without_ack_kernel_filter(monkeypatch):
    line = "12:00:00.000000 IP 172.16.5.40.50000 > 172.16.5.1.8080: Flags [S], seq 1\n"
    evidence, command, process = _run_observer(monkeypatch, [line], expected=receipt())

    assert command[-1] == "arp or (tcp[tcpflags] & (tcp-syn|tcp-ack) == tcp-syn)"
    assert evidence.kind == "expected"
    assert evidence.capture == line
    assert process.terminated


def test_passive_observer_classifies_syn_from_replacement_charger(monkeypatch):
    line = "12:00:00.000000 IP 192.168.50.20.50000 > 192.168.50.1.8888: Flags [S], seq 1\n"
    evidence, _, _ = _run_observer(monkeypatch, [line], expected=receipt())

    assert evidence.kind == "candidate"


def test_passive_observer_keeps_arp_as_candidate_wake_evidence(monkeypatch):
    line = "12:00:00.000000 aa:bb:cc:dd:ee:ff > ff:ff:ff:ff:ff:ff, ARP, Request who-has 10.42.0.1 tell 10.42.0.50, length 28\n"
    evidence, command, _ = _run_observer(monkeypatch, [line], expected=receipt())

    assert "arp" in command[-1]
    assert evidence.kind == "candidate"


def test_tcp_ack_and_payload_are_excluded_before_python(monkeypatch):
    """The BPF expression, rather than Python parsing, excludes established traffic."""
    line = "12:00:00.000000 IP 172.16.5.40.50000 > 172.16.5.1.8080: Flags [S], seq 1\n"
    _, command, _ = _run_observer(monkeypatch, [line])

    packet_filter = command[-1]
    assert "tcp-syn|tcp-ack" in packet_filter
    assert "== tcp-syn" in packet_filter
    assert packet_filter != "tcp or arp"
