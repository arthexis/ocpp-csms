"""Hermetic #355 sandbox tests. Never execute nft/bwrap or alter host networking."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ocpp_discover import lab_sandbox


def test_launcher_separates_namespaces_and_masks_host_state():
    cmd = lab_sandbox.sandbox_command(
        "/usr/bin/python3", "net:[1]", parent_mntns="mnt:[2]", parent_userns="user:[3]"
    )
    assert cmd[:5] == ["bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--clearenv"]
    assert cmd[5:8] == ["--ro-bind", "/", "/"]
    assert cmd.count("--tmpfs") == 4
    for directory in ("/tmp", "/run", "/var/lib", "/etc"):
        assert cmd[cmd.index(directory) - 1] == "--tmpfs"
    for kind in ("NETNS", "MNTNS", "USERNS"):
        assert "OCPP_DISCOVER_LAB_PARENT_" + kind in cmd
    assert "--share-net" not in cmd and "--bind" not in cmd
    assert cmd[-2:] == ["ocpp_discover.lab_sandbox", "--inside"]


@pytest.mark.parametrize("python,net", [
    ("python3", "net:[1]"), ("/usr/bin/python3", "forged"),
    ("/usr/bin/python3", "net:[nope]"),
])
def test_launcher_rejects_invalid_parameters(python, net):
    with pytest.raises(ValueError):
        lab_sandbox.sandbox_command(python, net)


def test_inside_without_attested_parent_is_denied(monkeypatch):
    monkeypatch.delenv("OCPP_DISCOVER_LAB_PARENT_MNTNS", raising=False)
    monkeypatch.delenv("OCPP_DISCOVER_LAB_PARENT_USERNS", raising=False)
    monkeypatch.setenv("OCPP_DISCOVER_LAB_PARENT_NETNS", "net:[21]")
    monkeypatch.setattr(lab_sandbox.os, "readlink", lambda path: "net:[22]")
    with pytest.raises(RuntimeError, match="lab_parent_namespace_not_attested"):
        lab_sandbox.require_sandbox()


def test_same_network_namespace_is_denied_before_nft(monkeypatch):
    monkeypatch.setenv("OCPP_DISCOVER_LAB_PARENT_NETNS", "net:[21]")
    monkeypatch.setenv("OCPP_DISCOVER_LAB_PARENT_MNTNS", "mnt:[23]")
    monkeypatch.setenv("OCPP_DISCOVER_LAB_PARENT_USERNS", "user:[25]")
    monkeypatch.setattr(lab_sandbox.os, "readlink", lambda path: "net:[21]")
    monkeypatch.setattr(lab_sandbox, "_nft", lambda *a, **kw: pytest.fail("nft reached"))
    with pytest.raises(RuntimeError, match="lab_net_namespace_not_isolated"):
        lab_sandbox.isolated_probe()


def test_actual_tmpfs_required_not_merely_mountpoint(monkeypatch):
    monkeypatch.setenv("OCPP_DISCOVER_LAB_PARENT_NETNS", "net:[1]")
    monkeypatch.setenv("OCPP_DISCOVER_LAB_PARENT_MNTNS", "mnt:[2]")
    monkeypatch.setenv("OCPP_DISCOVER_LAB_PARENT_USERNS", "user:[3]")
    monkeypatch.setattr(lab_sandbox.os, "readlink",
                        lambda path: {"net":"net:[10]", "mnt":"mnt:[20]", "user":"user:[30]"}[path.rsplit("/", 1)[-1]])
    fixture = "\n".join([
        "1 0 0:1 / / ro,relatime - overlay overlay ro",
        "2 1 0:2 / /tmp rw - tmpfs tmpfs rw",
        "3 1 0:3 / /run rw - tmpfs tmpfs rw",
        "4 1 0:4 / /var/lib rw - ext4 /dev/test rw",
        "5 1 0:5 / /etc rw - tmpfs tmpfs rw",
    ])
    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda p, *a, **kw: fixture if str(p) == "/proc/self/mountinfo" else original(p, *a, **kw))
    with pytest.raises(RuntimeError, match="lab_writable_state_not_tmpfs"):
        lab_sandbox.require_sandbox()


def nft_simulation(failure=None):
    state = {"exists": False}
    calls = []
    def invoke(args, text=None):
        calls.append(args)
        if args == ["list", "table", "ip", "ocpp_field_redirect"]:
            if not state["exists"]:
                raise subprocess.CalledProcessError(1, args, stderr="No such file or directory")
            if failure == "inspect":
                raise RuntimeError("nft_inspection_failure")
        if args == ["-f", "-"]:
            state["exists"] = True
            if failure == "apply":
                raise RuntimeError("nft_partial_apply_failure")
        if args == ["delete", "table", "ip", "ocpp_field_redirect"]:
            state["exists"] = False
        return SimpleNamespace(returncode=0)
    return invoke, calls, state


@pytest.mark.parametrize("failure", [None, "apply", "inspect"])
def test_synthetic_sqlite_and_nft_cleanup(monkeypatch, failure):
    nft, calls, state = nft_simulation(failure)
    monkeypatch.setattr(lab_sandbox, "require_sandbox", lambda: None)
    monkeypatch.setattr(lab_sandbox.shutil, "which", lambda name: "/usr/bin/nft")
    monkeypatch.setattr(lab_sandbox, "_nft", nft)
    if failure:
        with pytest.raises(RuntimeError, match="nft_"):
            lab_sandbox.isolated_probe()
    else:
        result = lab_sandbox.isolated_probe()
        assert result["disposable_sqlite"]
        assert result["network_packet_replay"] is False
        assert result["field_persistence"] is False
    assert not state["exists"]
    assert ["delete", "table", "ip", "ocpp_field_redirect"] in calls


def test_no_persistence_or_real_hardware_access():
    source = Path(lab_sandbox.__file__).read_text()
    assert "require_sandbox()" in source and "--unshare-all" in source
    assert "handoff._promote_persistent_adaptation" not in source
    assert "persist_discovered(" not in source
