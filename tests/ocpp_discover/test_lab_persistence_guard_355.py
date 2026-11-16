"""Fail-closed synthetic destination guard for #355.

These tests do not create network namespaces or touch SQLite, nftables or nodes.
An actual replay still requires separate disposable namespace/CSMS validation.
"""
from types import SimpleNamespace

import pytest

from ocpp_discover import service


@pytest.mark.parametrize("destination", [
    "198.18.0.1", "192.0.2.15", "198.51.100.8", "203.0.113.19",
])
def test_known_lab_destinations_cannot_promote(destination, tmp_path, monkeypatch):
    receipt = SimpleNamespace(destination_ips=[destination])
    writes = []
    monkeypatch.setattr(service.handoff, "_promote_persistent_adaptation",
                        lambda *args: writes.append(args))
    monkeypatch.setattr(service.diagnosis, "wait_for_discovery_evidence",
                        lambda interface, **kwargs: SimpleNamespace(capture="synthetic"))
    monkeypatch.setattr(service.discover, "run_discovery",
                        lambda **kwargs: SimpleNamespace(receipt=receipt, charger_id="EV-READY-G3",
                                                         to_json=lambda: {}))
    with pytest.raises(RuntimeError, match="synthetic_destination_cannot_be_persisted"):
        service._wait_then_discover(data_dir=tmp_path / "disposable-csms",
                                    runtime_dir=tmp_path / "runtime",
                                    persistent_dir=tmp_path / "field",
                                    interface="eth0", listen_port=9000,
                                    grace_seconds=0, arp_seconds=0, tcp_seconds=0,
                                    connect_timeout=0)
    assert writes == []
    assert not (tmp_path / "field").exists()


def test_non_lab_destination_not_promoted_without_proven_result(tmp_path, monkeypatch):
    writes = []
    monkeypatch.setattr(service.handoff, "_promote_persistent_adaptation",
                        lambda *args: writes.append(args))
    monkeypatch.setattr(service.diagnosis, "wait_for_discovery_evidence",
                        lambda interface, **kwargs: SimpleNamespace(capture="no match"))
    monkeypatch.setattr(service.discover, "run_discovery",
                        lambda **kwargs: SimpleNamespace(receipt=None, charger_id=None))
    with pytest.raises(RuntimeError, match="discovery_did_not_produce_proven_adaptation"):
        service._wait_then_discover(data_dir=tmp_path, runtime_dir=tmp_path,
                                    persistent_dir=tmp_path / "field", interface="eth0",
                                    listen_port=9000, grace_seconds=0, arp_seconds=0,
                                    tcp_seconds=0, connect_timeout=0)
    assert writes == []
