"""Stage B1 offline routed-HTTP opt-in contract; never call nftables."""
import json
import os
from pathlib import Path

import pytest

from ocpp_discover import routed_http_policy as policy


def payload(*, enabled=True, interface="eth0", verified=True):
    return {
        "version": 1,
        "enabled": enabled,
        "interface": interface,
        "dedicated_charger_network_verified": verified,
    }


def test_absent_config_is_disabled_and_does_not_create_file(tmp_path):
    path = tmp_path / "missing.json"
    result = policy.load_policy(path, owner_uid=os.getuid())
    assert not result.enabled
    assert not result.authorizes("eth0")
    assert not path.exists()


def test_explicit_verified_interface_only(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload()))
    path.chmod(0o600)
    result = policy.load_policy(path, owner_uid=os.getuid())
    assert result.authorizes("eth0")
    assert not result.authorizes("wlan0")


def test_disabled_config_never_authorizes(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload(enabled=False, verified=False)))
    path.chmod(0o600)
    assert not policy.load_policy(path, owner_uid=os.getuid()).authorizes("eth0")


@pytest.mark.parametrize("change", [
    {"enabled": "true"}, {"enabled": 1}, {"version": True},
    {"version": 2}, {"interface": "lo"}, {"interface": "eth0;reboot"},
    {"interface": "x" * 16}, {"dedicated_charger_network_verified": "true"},
    {"dedicated_charger_network_verified": False},
    {"surprise": True},
])
def test_invalid_policy_is_rejected(change):
    data = payload()
    data.update(change)
    with pytest.raises(policy.RoutedHttpPolicyError):
        policy.parse_policy(data)


def test_missing_required_property_is_rejected():
    data = payload()
    del data["interface"]
    with pytest.raises(policy.RoutedHttpPolicyError):
        policy.parse_policy(data)


@pytest.mark.parametrize("contents", [
    '{"version":1,"version":1,"enabled":true,"interface":"eth0","dedicated_charger_network_verified":true}',
    "{bad json",
])
def test_duplicate_or_malformed_json_fails_closed(tmp_path, contents):
    path = tmp_path / "config"
    path.write_text(contents)
    path.chmod(0o600)
    with pytest.raises(policy.RoutedHttpPolicyError):
        policy.load_policy(path, owner_uid=os.getuid())


@pytest.mark.parametrize("mode", [0o644, 0o666, 0o400])
def test_insecure_permissions_fail_closed(tmp_path, mode):
    path = tmp_path / "config"
    path.write_text(json.dumps(payload()))
    path.chmod(mode)
    with pytest.raises(policy.RoutedHttpPolicyError, match="permissions"):
        policy.load_policy(path, owner_uid=os.getuid())


def test_symlink_is_refused_even_if_target_is_private(tmp_path):
    actual = tmp_path / "actual"
    actual.write_text(json.dumps(payload()))
    actual.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(actual)
    with pytest.raises(policy.RoutedHttpPolicyError, match="untrusted"):
        policy.load_policy(link, owner_uid=os.getuid())


def test_wrong_owner_is_refused(tmp_path):
    path = tmp_path / "config"
    path.write_text(json.dumps(payload()))
    path.chmod(0o600)
    with pytest.raises(policy.RoutedHttpPolicyError, match="untrusted"):
        policy.load_policy(path, owner_uid=os.getuid() + 1)


def test_oversized_policy_is_refused(tmp_path):
    path = tmp_path / "config"
    path.write_bytes(b" " * 5000)
    path.chmod(0o600)
    with pytest.raises(policy.RoutedHttpPolicyError, match="oversized"):
        policy.load_policy(path, owner_uid=os.getuid())


def test_source_has_no_host_mutating_operations():
    source = Path(policy.__file__).read_text()
    assert "subprocess" not in source
    assert "systemctl" not in source
    assert "nft " not in source
    assert "os.replace" not in source
    assert "mkdir(" not in source
