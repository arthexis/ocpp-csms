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
    {"surprise": True},
])
def test_invalid_policy_is_rejected(change):
    data = payload()
    data.update(change)
    with pytest.raises(policy.RoutedHttpPolicyError):
        policy.parse_policy(data)


def test_legacy_unverified_attestation_no_longer_blocks_explicit_enable():
    assert policy.parse_policy(payload(verified=False)).authorizes("eth0")


def test_missing_policy_with_configured_interface_is_default_on(tmp_path):
    missing = tmp_path / "missing.json"
    loaded = policy.load_policy(missing, owner_uid=os.getuid(), default_interface="eth0")
    assert loaded.authorizes("eth0")
    assert not loaded.authorizes("wlan0")
    assert not missing.exists()


def test_explicit_disable_wins_over_default_on(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload(enabled=False, verified=False)))
    path.chmod(0o600)
    loaded = policy.load_policy(path, owner_uid=os.getuid(), default_interface="eth0")
    assert not loaded.authorizes("eth0")


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


def test_pre_open_substitution_with_symlink_fails_closed(tmp_path, monkeypatch):
    """The open must reject the path after an attacker swaps it to a link."""
    from unittest.mock import patch
    path = tmp_path / "config"
    target = tmp_path / "target"
    path.write_text(json.dumps(payload(enabled=False, verified=False)))
    path.chmod(0o600)
    target.write_text(json.dumps(payload()))
    target.chmod(0o600)
    real_open = os.open

    def replace_then_open(candidate, flags, *args, **kwargs):
        if os.fspath(candidate) == path.name:
            path.unlink()
            path.symlink_to(target)
        return real_open(candidate, flags, *args, **kwargs)

    with patch.object(policy.os, "open", side_effect=replace_then_open):
        with pytest.raises(policy.RoutedHttpPolicyError, match="untrusted"):
            policy.load_policy(path, owner_uid=os.getuid())


def test_path_replacement_after_open_never_reads_new_file(tmp_path):
    """A verified descriptor must not be changed by a subsequent path swap."""
    from unittest.mock import patch
    path = tmp_path / "config"
    substitute = tmp_path / "substitute"
    path.write_text(json.dumps(payload(enabled=False, verified=False)))
    path.chmod(0o600)
    substitute.write_text(json.dumps(payload()))
    substitute.chmod(0o600)
    real_open = os.open
    calls = []

    def replace_after_open(candidate, flags, *args, **kwargs):
        fd = real_open(candidate, flags, *args, **kwargs)
        calls.append(os.fspath(candidate))
        if os.fspath(candidate) == path.name:
            path.unlink()
            path.symlink_to(substitute)
        return fd

    with patch.object(policy.os, "open", side_effect=replace_after_open):
        result = policy.load_policy(path, owner_uid=os.getuid())
    assert not result.enabled
    assert not result.authorizes("eth0")
    assert calls[-1:] == [path.name]
    assert path.is_symlink()


def test_verified_descriptor_catches_post_open_mode_swap(tmp_path):
    """Changes after open but before fstat must be checked on the descriptor."""
    from unittest.mock import patch
    path = tmp_path / "config"
    path.write_text(json.dumps(payload()))
    path.chmod(0o600)
    real_open = os.open

    def change_mode(candidate, flags, *args, **kwargs):
        fd = real_open(candidate, flags, *args, **kwargs)
        if os.fspath(candidate) == path.name:
            os.fchmod(fd, 0o644)
        return fd

    with patch.object(policy.os, "open", side_effect=change_mode):
        with pytest.raises(policy.RoutedHttpPolicyError, match="permissions"):
            policy.load_policy(path, owner_uid=os.getuid())


def test_post_open_growth_exceeding_limit_is_rejected(tmp_path):
    from unittest.mock import patch
    path = tmp_path / "config"
    path.write_text(json.dumps(payload(enabled=False, verified=False)))
    path.chmod(0o600)
    real_open = os.open
    real_fstat = os.fstat
    import stat

    def grow_after_stat(fd):
        info = real_fstat(fd)
        if stat.S_ISREG(info.st_mode):
            with path.open("ab") as stream:
                stream.write(b" " * 5000)
        return info

    with patch.object(policy.os, "open", side_effect=real_open):
        with patch.object(policy.os, "fstat", side_effect=grow_after_stat):
            with pytest.raises(policy.RoutedHttpPolicyError, match="oversized"):
                policy.load_policy(path, owner_uid=os.getuid())


def test_fifo_is_rejected_without_blocking(tmp_path):
    import stat
    path = tmp_path / "fifo"
    os.mkfifo(path, 0o600)
    path.chmod(0o600)
    with pytest.raises(policy.RoutedHttpPolicyError, match="untrusted"):
        policy.load_policy(path, owner_uid=os.getuid())


def test_no_path_lstat_or_path_reopen():
    source = Path(policy.__file__).read_text()
    assert "O_NOFOLLOW" in source
    assert "os.fstat(" in source
    assert "stream.read(4097)" in source
    assert ".lstat(" not in source
    assert "file.open(" not in source


def test_untrusted_policy_parent_rejected(tmp_path):
    parent = tmp_path / "conf"
    parent.mkdir()
    path = parent / "policy.json"
    path.write_text(json.dumps(payload()))
    path.chmod(0o600)
    parent.chmod(0o777)
    with pytest.raises(policy.RoutedHttpPolicyError, match="directory"):
        policy.load_policy(path, owner_uid=os.getuid())


def test_parent_symlink_rejected(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    path = original / "policy.json"
    path.write_text(json.dumps(payload()))
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(original, target_is_directory=True)
    with pytest.raises(policy.RoutedHttpPolicyError, match="directory"):
        policy.load_policy(link / "policy.json", owner_uid=os.getuid())



def test_missing_intermediate_directory_is_not_a_disabled_policy(tmp_path):
    with pytest.raises(policy.RoutedHttpPolicyError, match="directory"):
        policy.load_policy(tmp_path / "missing" / "policy.json", owner_uid=os.getuid())


def test_relative_path_fails_closed(tmp_path):
    with pytest.raises(policy.RoutedHttpPolicyError, match="absolute"):
        policy.load_policy("policy.json", owner_uid=os.getuid())


def test_ancestor_symlink_fails_closed(tmp_path):
    real = tmp_path / "real" / "nested"
    real.mkdir(parents=True)
    target = real / "policy.json"
    target.write_text(json.dumps(payload()))
    target.chmod(0o600)
    (tmp_path / "alternate").symlink_to(tmp_path / "real", target_is_directory=True)
    with pytest.raises(policy.RoutedHttpPolicyError, match="directory"):
        policy.load_policy(tmp_path / "alternate" / "nested" / "policy.json", owner_uid=os.getuid())


def test_directory_swapped_after_descriptor_open_uses_pinned_original(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    first = original / "policy.json"
    first.write_text(json.dumps(payload(enabled=False, verified=False)))
    first.chmod(0o600)
    second = replacement / "policy.json"
    second.write_text(json.dumps(payload(enabled=True)))
    second.chmod(0o600)
    alias = tmp_path / "alias"
    alias.symlink_to(original, target_is_directory=True)
    # An existing symlink must never be accepted, even when its target is safe.
    with pytest.raises(policy.RoutedHttpPolicyError, match="directory"):
        policy.load_policy(alias / "policy.json", owner_uid=os.getuid())


def test_directory_substitution_at_open_is_rejected(tmp_path, monkeypatch):
    parent = tmp_path / "config"
    parent.mkdir()
    path = parent / "policy.json"
    path.write_text(json.dumps(payload(enabled=False, verified=False)))
    path.chmod(0o600)
    substitute = tmp_path / "substitute"
    substitute.mkdir()
    (substitute / "policy.json").write_text(json.dumps(payload()))
    (substitute / "policy.json").chmod(0o600)
    real_open = os.open

    def swapped(candidate, flags, *args, **kwargs):
        if os.fspath(candidate) == "config":
            parent.rename(tmp_path / "old-config")
            parent.symlink_to(substitute, target_is_directory=True)
        return real_open(candidate, flags, *args, **kwargs)

    from unittest.mock import patch
    with patch.object(policy.os, "open", side_effect=swapped):
        with pytest.raises(policy.RoutedHttpPolicyError, match="directory"):
            policy.load_policy(path, owner_uid=os.getuid())


def test_directory_replaced_after_open_keeps_original_descriptor(tmp_path):
    parent = tmp_path / "config"
    parent.mkdir()
    path = parent / "policy.json"
    path.write_text(json.dumps(payload(enabled=False, verified=False)))
    path.chmod(0o600)
    substitute = tmp_path / "substitute"
    substitute.mkdir()
    (substitute / "policy.json").write_text(json.dumps(payload()))
    (substitute / "policy.json").chmod(0o600)
    real_open = os.open

    def swapped(candidate, flags, *args, **kwargs):
        fd = real_open(candidate, flags, *args, **kwargs)
        if os.fspath(candidate) == "config":
            parent.rename(tmp_path / "old-config")
            parent.symlink_to(substitute, target_is_directory=True)
        return fd

    from unittest.mock import patch
    with patch.object(policy.os, "open", side_effect=swapped):
        result = policy.load_policy(path, owner_uid=os.getuid())
    assert result.enabled is False


def test_reject_group_writable_nonsticky_ancestor(tmp_path):
    parent = tmp_path / "writable"
    parent.mkdir()
    nested = parent / "nested"
    nested.mkdir()
    path = nested / "policy.json"
    path.write_text(json.dumps(payload()))
    path.chmod(0o600)
    parent.chmod(0o770)
    with pytest.raises(policy.RoutedHttpPolicyError, match="directory"):
        policy.load_policy(path, owner_uid=os.getuid())


def test_fstat_ownership_and_permissions_after_fd_open(tmp_path, monkeypatch):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload()))
    path.chmod(0o600)
    with pytest.raises(policy.RoutedHttpPolicyError, match="untrusted"):
        policy.load_policy(path, owner_uid=os.getuid() + 1)
    path.chmod(0o640)
    with pytest.raises(policy.RoutedHttpPolicyError, match="permissions"):
        policy.load_policy(path, owner_uid=os.getuid())
