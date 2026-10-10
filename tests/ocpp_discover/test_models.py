"""Public discovery models retain their historical import paths."""

from ocpp_discover import discover
from ocpp_discover.models import AddressClaim, DiscoveryCandidate, DiscoveryResult


def test_discovery_models_remain_publicly_importable():
    assert discover.AddressClaim is AddressClaim
    assert discover.DiscoveryCandidate is DiscoveryCandidate
    assert discover.DiscoveryResult is DiscoveryResult


def test_discovery_model_json_contracts():
    candidate = DiscoveryCandidate("eth0", "aa:bb:cc:dd:ee:ff", "192.0.2.2", "192.0.2.1", 2)
    assert candidate.to_json() == {
        "interface": "eth0", "source_mac": "aa:bb:cc:dd:ee:ff",
        "source_ip": "192.0.2.2", "target_ip": "192.0.2.1", "requests": 2,
    }
    claim = AddressClaim("eth0", "192.0.2.1")
    assert claim.to_json() == {"interface": "eth0", "address": "192.0.2.1"}
    assert DiscoveryResult("found", "CP1", candidate).to_json() == {
        "status": "found", "charger_id": "CP1", "candidate": candidate.to_json(), "redirect": None,
    }
