"""Issue #355: default-on and bounded first-contact retries without host I/O."""
from types import SimpleNamespace

import pytest

from ocpp_discover import diagnosis, routed_http_runtime as runtime, service
from ocpp_discover.routed_http_policy import RoutedHttpPolicy
from ocpp_discover.routed_http_topology import InterfaceTopology


def _physical(interface="eth0", index=7):
    topology = InterfaceTopology(interface, True, "ethernet", True, None, (), (interface,), True, True)
    return runtime.KernelObservation(interface, index, topology, False)


def test_no_external_peer_attestation_required(monkeypatch):
    monkeypatch.setattr(runtime, "collect_kernel_observation", lambda name: _physical(name))
    authorized = runtime.authorize_routed_http(RoutedHttpPolicy(True, "eth0"), "eth0")
    assert authorized is not None
    assert authorized.authorizes("eth0")
    assert runtime.still_authorized(authorized, "eth0")
    assert not runtime.still_authorized(authorized, "wlan0")
    monkeypatch.setattr(runtime, "collect_kernel_observation", lambda name: _physical(name, 8))
    assert not runtime.still_authorized(authorized, "eth0")


@pytest.mark.parametrize("reason", [
    "no_plaintext_websocket_upgrade",
    "secure_or_opaque_traffic",
    "provisional_redirect_unproven",
])
def test_inconclusive_first_contact_defers_without_restart(tmp_path, monkeypatch, reason):
    attempts = []
    delays = []
    monkeypatch.setattr(service.routed_http_policy, "load_policy",
                        lambda **kwargs: RoutedHttpPolicy(False, "eth0"))
    monkeypatch.setattr(service, "_wait_then_discover",
                        lambda **kwargs: attempts.append(kwargs) or (_ for _ in ()).throw(RuntimeError(reason)))
    monkeypatch.setattr(service.time, "sleep", lambda seconds: delays.append(seconds))
    outcome = service.run_service(data_dir=tmp_path, persistent_dir=tmp_path / "state",
                                  wait_interval=2, max_cycles=3)
    assert outcome == {"status": "deferred", "reason": reason, "chargers": []}
    assert len(attempts) == 3
    assert delays == [2, 4]


def test_unexpected_first_contact_error_remains_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(service.routed_http_policy, "load_policy",
                        lambda **kwargs: RoutedHttpPolicy(False, "eth0"))
    monkeypatch.setattr(service, "_wait_then_discover",
                        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("sqlite_corruption")))
    with pytest.raises(RuntimeError, match="sqlite_corruption"):
        service.run_service(data_dir=tmp_path, persistent_dir=tmp_path / "state", max_cycles=2)
