import pytest
from types import SimpleNamespace

from ocpp_discover import diagnosis, first_contact, service
from ocpp_discover.redirect import RedirectReceipt, WebSocketRequest


SYN = "12:00:00.000000 IP 192.168.50.20.50000 > 192.168.50.1.8888: Flags [S], seq 1\n"


def adaptation():
    return RedirectReceipt(interface="enp7s0", listen_port=9100, source_ip="192.168.50.20", destination_ips=["192.168.50.1"], requests=[WebSocketRequest("192.168.50.1", "192.168.50.1:8888", "/ocpp/CP7")], captured_at="discovered", destination_port=8888)


def result():
    return SimpleNamespace(charger_id="CP7", receipt=adaptation(), to_json=lambda: {"status": "connected"})


def run_adapter(monkeypatch, *, initial="first\n", later="later\n", captures=1):
    replacement = lambda interface, seconds: later
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", replacement)
    observed = []

    def run_discovery(**kwargs):
        for _ in range(captures):
            observed.append(first_contact.core.capture_passive_tcp("eth0", 1))
        return object()

    monkeypatch.setattr(first_contact.core, "run_discovery", run_discovery)
    first_contact.run_discovery(initial_evidence=initial, data_dir="data", state_dir="state")
    return observed, replacement


def test_service_passes_initial_syn_into_discovery(tmp_path, monkeypatch):
    evidence = diagnosis.DiscoveryEvidence("candidate", SYN)
    monkeypatch.setattr(service.diagnosis, "wait_for_discovery_evidence", lambda interface: evidence)
    calls = []
    monkeypatch.setattr(service.discover, "run_discovery", lambda **kwargs: calls.append(kwargs) or result())
    monkeypatch.setattr(service.handoff, "_promote_persistent_adaptation", lambda *args, **kwargs: None)

    outcome = service.run_service(
        data_dir=tmp_path / "data",
        runtime_dir=tmp_path / "runtime",
        persistent_dir=tmp_path / "persistent",
        interface="enp7s0",
        listen_port=9100,
        max_cycles=1,
    )

    assert outcome["status"] == "discovered"
    assert len(calls) == 1
    assert calls[0]["initial_evidence"] == SYN


def test_service_preserves_complete_qualified_arp_capture(tmp_path, monkeypatch):
    first = "12:00:00.000000 ARP, Request who-has 10.42.0.1 tell 10.42.0.50, length 28\n"
    second = "12:00:01.000000 ARP, Request who-has 10.42.0.1 tell 10.42.0.50, length 28\n"
    evidence = diagnosis.DiscoveryEvidence("candidate", first + second)
    monkeypatch.setattr(service.diagnosis, "wait_for_discovery_evidence", lambda interface: evidence)
    calls = []
    monkeypatch.setattr(service.discover, "run_discovery", lambda **kwargs: calls.append(kwargs) or result())
    monkeypatch.setattr(service.handoff, "_promote_persistent_adaptation", lambda *args, **kwargs: None)

    service.run_service(
        data_dir=tmp_path / "data",
        runtime_dir=tmp_path / "runtime",
        persistent_dir=tmp_path / "persistent",
        interface="enp7s0",
        listen_port=9100,
        max_cycles=1,
    )

    assert calls[0]["initial_evidence"] == first + second


@pytest.mark.parametrize(
    ("initial", "later", "expected"),
    [
        ("first\n", "second\n", "first\nsecond\n"),
        (SYN, "", SYN),
    ],
)
def test_adapter_seeds_first_capture_with_initial_evidence(monkeypatch, initial, later, expected):
    observed, _ = run_adapter(monkeypatch, initial=initial, later=later)
    assert observed == [expected]


def test_adapter_prepends_initial_evidence_only_once(monkeypatch):
    observed, _ = run_adapter(monkeypatch, captures=2)
    assert observed == ["first\nlater\n", "later\n"]


def test_adapter_restores_capture_after_success(monkeypatch):
    _, replacement = run_adapter(monkeypatch)
    assert first_contact.core.capture_passive_tcp is replacement


def test_adapter_restores_capture_after_failure(monkeypatch):
    replacement = lambda interface, seconds: "later\n"
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", replacement)
    monkeypatch.setattr(
        first_contact.core,
        "run_discovery",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        first_contact.run_discovery(initial_evidence=SYN, data_dir="data", state_dir="state")

    assert first_contact.core.capture_passive_tcp is replacement


def test_adapter_requires_initial_evidence():
    with pytest.raises(ValueError, match="initial_evidence_required"):
        first_contact.run_discovery(initial_evidence="", data_dir="data", state_dir="state")
