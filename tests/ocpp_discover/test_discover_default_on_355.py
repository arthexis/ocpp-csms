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


def test_interface_down_at_boot_then_up_mints_scoped_authority(tmp_path, monkeypatch):
    from ocpp_discover import handoff
    seen = []
    delayed = iter([None, None, None, _physical("eth0", 7)])
    monkeypatch.setattr(service, "_configured_link_is_down", lambda interface: True)
    monkeypatch.setattr(service.routed_http_policy, "load_policy",
                        lambda **kwargs: RoutedHttpPolicy(True, "eth0"))
    monkeypatch.setattr(service.routed_http_runtime, "authorize_routed_http",
                        lambda policy, interface: delayed.__next__())
    monkeypatch.setattr(service, "_wait_then_discover",
                        lambda **kwargs: seen.append(kwargs["policy"]) or
                        {"status": "discovered", "chargers": ["CP7"]})
    monkeypatch.setattr(service.handoff, "discovered_path",
                        lambda persistent_dir: tmp_path / "no-persistence")
    # _wait_then_discover is stubbed to return a result without actual persistence.
    monkeypatch.setattr(service.time, "sleep", lambda interval: seen.append(interval))
    result = service.run_service(data_dir=tmp_path, persistent_dir=tmp_path / "state",
                                 wait_interval=1, max_cycles=3)
    assert result["status"] == "discovered"
    assert seen[:2] == [1.0, 2.0]
    assert isinstance(seen[2], runtime.KernelObservation)


def test_missing_interface_remains_deferred_with_bounded_attempts(tmp_path, monkeypatch):
    delays = []
    monkeypatch.setattr(service.routed_http_policy, "load_policy",
                        lambda **kwargs: RoutedHttpPolicy(True, "eth0"))
    monkeypatch.setattr(service.routed_http_runtime, "authorize_routed_http",
                        lambda policy, interface: None)
    monkeypatch.setattr(service, "_configured_link_is_down", lambda interface: True)
    monkeypatch.setattr(service.diagnosis, "wait_for_discovery_evidence",
                        lambda *a, **kw: pytest.fail("must not wait without permission"))
    monkeypatch.setattr(service.time, "sleep", lambda interval: delays.append(interval))
    outcome = service.run_service(data_dir=tmp_path, persistent_dir=tmp_path / "state",
                                  wait_interval=1, max_cycles=3)
    assert outcome["reason"] == "routed_http_interface_unavailable"
    assert delays == [1.0, 2.0]
