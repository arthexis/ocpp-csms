"""Hermetic adversarial tests for the #355 synthetic sandbox contract.

Never start bubblewrap, nft, services, devices, or network namespaces in CI.
"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from ocpp_discover import lab_sandbox


def test_launcher_requires_all_isolation_layers():
    cmd = lab_sandbox.sandbox_command("/usr/bin/python3", "net:[4026532000]")
    assert cmd[:4] == ["bwrap", "--die-with-parent", "--new-session", "--unshare-all"]
    assert ["--ro-bind", "/", "/"] == cmd[4:7]
    for path in ("/tmp", "/run", "/var/lib", "/etc"):
        assert cmd.count("--tmpfs") == 4
        position = cmd.index(path)
        assert cmd[position - 1] == "--tmpfs"
    assert "--share-net" not in cmd
    assert "--bind" not in cmd
    assert "--ro-bind" in cmd
    assert "--proc" in cmd and "--dev" in cmd
    assert "--" in cmd and cmd[-2:] == ["ocpp_discover.lab_sandbox", "--inside"]


@pytest.mark.parametrize("python,netns", [
    ("python3", "net:[123]"),
    ("/usr/bin/python3", ""),
    ("/usr/bin/python3", "forged"),
])
def test_malformed_launcher_configuration_fails_closed(python, netns):
    with pytest.raises(ValueError):
        lab_sandbox.sandbox_command(python, netns)


def test_parent_netns_is_rejected_before_any_nft(monkeypatch):
    monkeypatch.setenv("OCPP_DISCOVER_LAB_PARENT_NETNS", "net:[23]")
    monkeypatch.setattr(lab_sandbox.os, "readlink", lambda path: "net:[23]")
    monkeypatch.setattr(lab_sandbox, "_nft", lambda *a, **k: pytest.fail("nft in host ns"))
    with pytest.raises(RuntimeError, match="lab_network_namespace_not_isolated"):
        lab_sandbox.isolated_probe()


def test_distinct_netns_but_non_tmpfs_state_is_rejected(monkeypatch):
    monkeypatch.setenv("OCPP_DISCOVER_LAB_PARENT_NETNS", "net:[23]")
    monkeypatch.setattr(lab_sandbox.os, "readlink", lambda path: "net:[24]")
    monkeypatch.setattr(lab_sandbox.os.path, "ismount", lambda path: path != "/var/lib")
    with pytest.raises(RuntimeError, match="lab_writable_state_not_tmpfs"):
        lab_sandbox.require_sandbox()


def test_failed_sandbox_check_does_not_spawn_subprocess(monkeypatch):
    monkeypatch.setenv("OCPP_DISCOVER_LAB_PARENT_NETNS", "net:[23]")
    monkeypatch.setattr(lab_sandbox.os, "readlink", lambda path: "net:[23]")
    monkeypatch.setattr(lab_sandbox, "_nft", lambda *a, **k: pytest.fail("nft in host ns"))
    monkeypatch.setattr(lab_sandbox.subprocess, "run", lambda *a, **k: pytest.fail("spawn"))
    with pytest.raises(RuntimeError, match="lab_network_namespace_not_isolated"):
        lab_sandbox.isolated_probe()


def test_synthetic_trace_uses_only_disposable_sqlite_and_mocked_firewall(monkeypatch):
    calls = []
    monkeypatch.setattr(lab_sandbox, "require_sandbox", lambda: None)
    monkeypatch.setattr(lab_sandbox.shutil, "which", lambda name: "/usr/bin/nft")
    monkeypatch.setattr(lab_sandbox, "_nft", lambda args, text=None:
                        calls.append((args, text)) or SimpleNamespace(returncode=0))
    result = lab_sandbox.isolated_probe()
    assert result["isolated"] and result["disposable_sqlite"]
    assert result["field_persistence"] is False
    assert result["network_packet_replay"] is False
    assert [entry[0] for entry in calls] == [
        ["-c", "-f", "-"], ["-f", "-"],
        ["list", "table", "ip", "ocpp_field_redirect"],
        ["delete", "table", "ip", "ocpp_field_redirect"]
    ]
    assert 'iifname "eth0"' in calls[0][1]
    assert "tcp dport 80" in calls[0][1]
    assert "tcp dport 22" not in calls[0][1]
    assert "tcp dport 443" not in calls[0][1]


def test_nft_failure_does_not_claim_clean_replay(monkeypatch):
    calls = []
    monkeypatch.setattr(lab_sandbox, "require_sandbox", lambda: None)
    monkeypatch.setattr(lab_sandbox.shutil, "which", lambda name: "/usr/bin/nft")
    def nft(args, text=None):
        calls.append(args)
        if args == ["list", "table", "ip", "ocpp_field_redirect"]:
            raise RuntimeError("nft_probe_failed")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(lab_sandbox, "_nft", nft)
    with pytest.raises(RuntimeError, match="nft_probe_failed"):
        lab_sandbox.isolated_probe()
    assert calls[-1] == ["delete", "table", "ip", "ocpp_field_redirect"]


def test_no_in_process_host_mutation_without_inner_isolation():
    source = Path(lab_sandbox.__file__).read_text()
    assert "require_sandbox()" in source
    assert "--unshare-all" in source
    assert '"--ro-bind", "/", "/"' in source
    assert "handoff._promote_persistent_adaptation" not in source
    assert "persist_discovered(" not in source
