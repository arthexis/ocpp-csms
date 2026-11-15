"""B2-B2: hermetic local topology reader and effective authority gates."""
from dataclasses import replace

import pytest

from ocpp_discover import routed_http_runtime as runtime, provisional
from ocpp_discover.routed_http_policy import RoutedHttpPolicy


def test_operator_policy_and_forged_topology_are_insufficient(monkeypatch):
    monkeypatch.setenv("OCPP_DISCOVER_ALLOW_ROUTED_HTTP_80", "1")
    assert runtime.authorize_routed_http(RoutedHttpPolicy(True, "eth0"), "eth0") is None
    assert not provisional.routed_http_enabled("eth0", RoutedHttpPolicy(True, "eth0"))
    assert not runtime.still_authorized(None, "eth0")
    assert not runtime.still_authorized({"interface": "eth0"}, "eth0")


def test_collector_reads_only_local_kernel_facts(tmp_path, monkeypatch):
    sys = tmp_path / "net"
    iface = sys / "eth0"
    iface.mkdir(parents=True)
    for name, value in {"ifindex": "7", "iflink": "7", "type": "1", "operstate": "up"}.items():
        (iface / name).write_text(value)
    (iface / "device").mkdir()
    routes = tmp_path / "route"
    routes.write_text("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\neth0\t00000000\t00000000\t0001\t0\t0\t0\t00000000\n")
    monkeypatch.setattr(runtime, "_SYS_NET", sys)
    monkeypatch.setattr(runtime, "_PROC_ROUTE", routes)
    observation = runtime.collect_kernel_observation("eth0")
    assert observation.ifindex == 7
    assert observation.topology.physical
    assert not observation.peer_exclusivity_proven
    assert observation.topology.non_charger_peers
    assert runtime.authorize_routed_http(RoutedHttpPolicy(True, "eth0"), "eth0") is None


@pytest.mark.parametrize("change", [{"ifindex": "8"}, {"iflink": "99"}, {"operstate": "down"},
                                      {"type": "772"}])
def test_collector_invalid_kernel_facts_do_not_authorize(tmp_path, monkeypatch, change):
    iface = tmp_path / "net" / "eth0"
    iface.mkdir(parents=True)
    for name, value in {"ifindex": "7", "iflink": "7", "type": "1", "operstate": "up", **change}.items():
        (iface / name).write_text(value)
    routes = tmp_path / "route"
    routes.write_text("Iface\tDestination\neth0\t0\n")
    monkeypatch.setattr(runtime, "_SYS_NET", tmp_path / "net")
    monkeypatch.setattr(runtime, "_PROC_ROUTE", routes)
    assert runtime.authorize_routed_http(RoutedHttpPolicy(True, "eth0"), "eth0") is None


def test_missing_interface_or_route_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_SYS_NET", tmp_path)
    monkeypatch.setattr(runtime, "_PROC_ROUTE", tmp_path / "missing")
    assert runtime.collect_kernel_observation("missing") is None
    assert runtime.collect_kernel_observation("../etc") is None


def test_routed_apply_rejects_before_any_nft(monkeypatch):
    candidate = provisional.LocalSynCandidate("eth0", "192.168.129.2", "192.0.2.4", 80, 9000)
    monkeypatch.setattr(provisional.redirect, "require_root", lambda: pytest.fail("no nft or root on denial"))
    with pytest.raises(RuntimeError, match="routed_http_topology_unverified"):
        provisional.apply(candidate)
    with pytest.raises(RuntimeError, match="routed_http_topology_unverified"):
        provisional.apply(candidate, routed_http_authorization=RoutedHttpPolicy(True, "eth0"))


def test_unsealed_authorization_is_rejected():
    forged = runtime.EffectiveRoutedHttpAuthorization("eth0", 7, object())
    assert not provisional.routed_http_enabled("eth0", forged)
    assert not runtime.still_authorized(forged, "eth0")


def test_collector_does_not_contain_external_mutators():
    from pathlib import Path
    code = Path(runtime.__file__).read_text()
    for forbidden in ("subprocess", "systemctl", "nft ", "os.system", "os.replace", "os.open("):
        assert forbidden not in code


def test_runtime_authorization_rechecks_ifindex_before_apply(monkeypatch):
    from ocpp_discover.routed_http_topology import InterfaceTopology
    def observed(index):
        topology = InterfaceTopology(
            "eth0", True, "ethernet", True, None, (), ("eth0",), False, False
        )
        return runtime.KernelObservation("eth0", index, topology, True)
    monkeypatch.setattr(runtime, "collect_kernel_observation", lambda name: observed(7))
    authority = runtime.authorize_routed_http(RoutedHttpPolicy(True, "eth0"), "eth0")
    assert authority is not None
    assert runtime.still_authorized(authority, "eth0")
    assert not runtime.still_authorized(authority, "wlan0")
    monkeypatch.setattr(runtime, "collect_kernel_observation", lambda name: observed(8))
    assert not runtime.still_authorized(authority, "eth0")
    candidate = provisional.LocalSynCandidate("eth0", "192.168.129.2", "192.0.2.4", 80, 9000)
    monkeypatch.setattr(provisional.redirect, "require_root", lambda: pytest.fail("no nft on drift"))
    with pytest.raises(RuntimeError, match="routed_http_topology_unverified"):
        provisional.apply(candidate, routed_http_authorization=authority)


def test_routed_candidate_needs_effective_authority(monkeypatch):
    from ocpp_discover.routed_http_topology import InterfaceTopology
    topology = InterfaceTopology("eth0", True, "ethernet", True, None, (), ("eth0",), False, False)
    monkeypatch.setattr(runtime, "collect_kernel_observation",
                        lambda name: runtime.KernelObservation("eth0", 7, topology, True))
    authority = runtime.authorize_routed_http(RoutedHttpPolicy(True, "eth0"), "eth0")
    monkeypatch.setattr(provisional.discover, "host_addresses", lambda: {"192.168.129.1"})
    line = "12:00:00.000000 IP 192.168.129.184.50000 > 192.0.2.1.80: Flags [S], seq 1\\n"
    line = line.replace("\\\\n", "\\n")
    candidate = provisional.local_syn_candidate(line, interface="eth0", listen_port=9000, routed_http_policy=authority)
    assert candidate is not None
    assert 'iifname "eth0"' in provisional._ruleset(candidate)
    assert "tcp dport 80 redirect to :9000" in provisional._ruleset(candidate)
    for port in (22, 443):
        other = line.replace(".80:", f".{port}:")
        assert provisional.local_syn_candidate(other, interface="eth0", listen_port=9000, routed_http_policy=authority) is None
