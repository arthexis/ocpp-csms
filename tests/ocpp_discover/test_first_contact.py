from types import SimpleNamespace

from ocpp_discover import diagnosis, service


SYN = "12:00:00.000000 IP 192.168.50.20.50000 > 192.168.50.1.8888: Flags [S], seq 1\n"


def test_first_discovery_passes_initial_syn_into_discovery(tmp_path, monkeypatch):
    """The SYN that wakes Discover must seed discovery; no second SYN is available."""
    evidence = diagnosis.DiscoveryEvidence("candidate", SYN)
    monkeypatch.setattr(service.diagnosis, "wait_for_discovery_evidence", lambda interface: evidence)
    calls = []

    def run_discovery(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(to_json=lambda: {"status": "connected"})

    monkeypatch.setattr(service.discover, "run_discovery", run_discovery)
    outcome = service.run_service(
        data_dir=tmp_path / "data",
        runtime_dir=tmp_path / "runtime",
        persistent_dir=tmp_path / "persistent",
        interface="enp7s0",
        listen_port=9100,
    )

    assert outcome["status"] == "discovered"
    assert len(calls) == 1
    assert calls[0]["initial_evidence"] == SYN


def test_first_discovery_preserves_qualified_arp_capture(tmp_path, monkeypatch):
    first = "12:00:00.000000 ARP, Request who-has 10.42.0.1 tell 10.42.0.50, length 28\n"
    second = "12:00:01.000000 ARP, Request who-has 10.42.0.1 tell 10.42.0.50, length 28\n"
    evidence = diagnosis.DiscoveryEvidence("candidate", first + second)
    monkeypatch.setattr(service.diagnosis, "wait_for_discovery_evidence", lambda interface: evidence)
    calls = []
    monkeypatch.setattr(
        service.discover,
        "run_discovery",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(to_json=lambda: {"status": "connected"}),
    )

    service.run_service(
        data_dir=tmp_path / "data",
        runtime_dir=tmp_path / "runtime",
        persistent_dir=tmp_path / "persistent",
        interface="enp7s0",
        listen_port=9100,
    )

    assert calls[0]["initial_evidence"] == first + second
