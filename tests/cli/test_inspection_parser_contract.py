"""Inspection commands share read-only query contracts."""
from ocpp_csms.cli import build_parser
from ocpp_csms.cli.inspection import capability_request


def test_capability_request_is_consistent_and_read_only():
    assert capability_request("CP1") == {
        "command": "config", "keys": ["SupportedFeatureProfiles"],
        "force": True, "charger": "CP1",
    }
    assert capability_request() == {
        "command": "config", "keys": ["SupportedFeatureProfiles"], "force": True,
    }


def test_inspect_capabilities_reconcile_use_canonical_cp():
    parser, _ = build_parser()
    for command in ("inspect", "capabilities", "reconcile"):
        assert parser.parse_args([command, "--cp", "CP1", "-j"]).charger == "CP1"
        assert parser.parse_args([command, "--cp", "CP2", "--json"]).json
