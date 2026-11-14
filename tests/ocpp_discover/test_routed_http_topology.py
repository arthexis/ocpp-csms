"""B2-B1 hermetic topology contract: no real device or network inspection."""
from dataclasses import replace

import pytest

from ocpp_discover import routed_http_topology as topology


def dedicated():
    return topology.InterfaceTopology(
        interface="eth0",
        exists=True,
        kind="ethernet",
        physical=True,
        master=None,
        lower_interfaces=(),
        route_interfaces=("eth0",),
        shared_subnet=False,
        non_charger_peers=False,
    )


def test_exclusive_physical_ethernet_passes():
    assert topology.dedicated_interface_verified(dedicated())


@pytest.mark.parametrize("overrides", [
    {"exists": False},
    {"kind": "bridge", "physical": False},
    {"kind": "veth", "physical": False},
    {"kind": "vlan", "physical": False},
    {"kind": "bond", "physical": False},
    {"physical": False},
    {"master": "br0"},
    {"lower_interfaces": ("eth1",)},
    {"route_interfaces": ("eth0", "wlan0")},
    {"route_interfaces": ("wlan0",)},
    {"route_interfaces": ()},
    {"shared_subnet": True},
    {"non_charger_peers": True},
    {"exists": 1},
    {"route_interfaces": ["eth0"]},
    {"shared_subnet": 0},
    {"interface": ""},
])
def test_unverified_or_shared_topology_fails_closed(overrides):
    assert not topology.dedicated_interface_verified(replace(dedicated(), **overrides))


def test_unknown_and_missing_evidence_fail_closed():
    assert not topology.dedicated_interface_verified(None)
    assert not topology.dedicated_interface_verified({"interface": "eth0"})


def test_topology_contract_has_no_host_commands():
    from pathlib import Path
    source = Path(topology.__file__).read_text()
    assert "subprocess" not in source
    assert "socket" not in source
    assert "netlink" not in source
    assert "nft " not in source
