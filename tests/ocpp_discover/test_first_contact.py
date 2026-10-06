from types import SimpleNamespace

from ocpp_discover import diagnosis, first_contact, service
from ocpp_discover.redirect import RedirectReceipt, WebSocketRequest


SYN = "12:00:00.000000 IP 192.168.50.20.50000 > 192.168.50.1.8888: Flags [S], seq 1\n"


def adaptation():
    return RedirectReceipt(interface="enp7s0", listen_port=9100, source_ip="192.168.50.20", destination_ips=["192.168.50.1"], requests=[WebSocketRequest("192.168.50.1", "192.168.50.1:8888", "/ocpp/CP7")], captured_at="discovered", destination_port=8888)


def result():
    return SimpleNamespace(charger_id="CP7", receipt=adaptation(), to_json=lambda: {"status": "connected"})


def test_first_discovery_passes_initial_syn_into_discovery(tmp_path, monkeypatch):
    """The SYN that wakes Discover must seed discovery; no second SYN is available."""
    evidence = diagnosis.DiscoveryEvidence("candidate", SYN)
    monkeypatch.setattr(service.diagnosis, "wait_for_discovery_evidence", lambda interface: evidence)
    calls = []
    monkeypatch.setattr(service.discover, "run_discovery", lambda **kwargs: calls.append(kwargs) or result())
    monkeypatch.setattr(service.handoff, "_promote_persistent_adaptation", lambda *args, **kwargs: None)
    outcome = service.run_service(data_dir=tmp_path / "data", runtime_dir=tmp_path / "runtime", persistent_dir=tmp_path / "persistent", interface="enp7s0", listen_port=9100, max_cycles=1)
    assert outcome["status"] == "discovered"
    assert len(calls) == 1
    assert calls[0]["initial_evidence"] == SYN


def test_first_discovery_preserves_qualified_arp_capture(tmp_path, monkeypatch):
    first = "12:00:00.000000 ARP, Request who-has 10.42.0.1 tell 10.42.0.50, length 28\n"
    second = "12:00:01.000000 ARP, Request who-has 10.42.0.1 tell 10.42.0.50, length 28\n"
    evidence = diagnosis.DiscoveryEvidence("candidate", first + second)
    monkeypatch.setattr(service.diagnosis, "wait_for_discovery_evidence", lambda interface: evidence)
    calls = []
    monkeypatch.setattr(service.discover, "run_discovery", lambda **kwargs: calls.append(kwargs) or result())
    monkeypatch.setattr(service.handoff, "_promote_persistent_adaptation", lambda *args, **kwargs: None)
    service.run_service(data_dir=tmp_path / "data", runtime_dir=tmp_path / "runtime", persistent_dir=tmp_path / "persistent", interface="enp7s0", listen_port=9100, max_cycles=1)
    assert calls[0]["initial_evidence"] == first + second


def test_adapter_prepends_first_contact_to_first_bounded_tcp_capture(monkeypatch):
    seen = []
    original = first_contact.core.capture_passive_tcp
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", lambda interface, seconds: "later-capture\n")

    def run_discovery(**kwargs):
        seen.append(first_contact.core.capture_passive_tcp("enp7s0", 5))
        return "result"

    monkeypatch.setattr(first_contact.core, "run_discovery", run_discovery)
    assert first_contact.run_discovery(initial_evidence=SYN, data_dir="data", state_dir="state") == "result"
    assert seen == [SYN + "later-capture\n"]
    assert first_contact.core.capture_passive_tcp is not original


def test_adapter_restores_capture_function_after_failure(monkeypatch):
    replacement = lambda interface, seconds: "later-capture\n"
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", replacement)
    monkeypatch.setattr(first_contact.core, "run_discovery", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        first_contact.run_discovery(initial_evidence=SYN, data_dir="data", state_dir="state")
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected failure")
    assert first_contact.core.capture_passive_tcp is replacement
