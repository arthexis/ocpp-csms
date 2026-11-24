import json
import socket

import pytest

from ocpp_csms.mcp_manifest import check_previous, manifest, probe
from ocpp_csms.evidence.store import EventStore


@pytest.fixture
def release(tmp_path):
    revision = "a" * 40
    root = tmp_path / revision[:12]
    exe = root / "venv/bin/ocpp-csms-mcp"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    current = tmp_path / "current"
    current.symlink_to(root)
    data = tmp_path / "schemas/8"
    data.mkdir(parents=True)
    return revision, exe, current, data


def test_exact_gway_v1_contract_and_rollback(release):
    revision, exe, current, data = release
    read = manifest(str(exe), revision, str(data), "read")
    assert set(read) == {"source_id", "revision", "executable", "argv"}
    assert read["source_id"] == "ocpp"
    assert read["argv"] == [str(exe), "--data-dir", str(data)]
    assert json.dumps(read, sort_keys=True) == json.dumps(manifest(str(exe), revision, str(data), "read"), sort_keys=True)
    check_previous(read, current, str(data))
    active = manifest(str(exe), revision, str(data), "execute")
    assert active["argv"][-1] == "--enable-execute"
    check_previous(active, current, str(data))
    with pytest.raises(ValueError, match="exceeds requested policy"):
        check_previous(active, current, str(data), max_mode="read")
    with pytest.raises(ValueError):
        check_previous(active, current, str(data.parent / "7"))
    with pytest.raises(ValueError):
        check_previous({**active, "revision": "b" * 40}, current, str(data))


def test_unsafe_provider_denied(release):
    revision, exe, current, data = release
    exe.chmod(0o777)
    with pytest.raises(ValueError):
        manifest(str(exe), revision, str(data), "read")
    exe.chmod(0o755)
    with pytest.raises(ValueError):
        manifest(str(current / "venv/bin/ocpp-csms-mcp"), "short", str(data), "read")
    link = exe.parent.parent / "ocpp-csms-mcp"
    link.symlink_to(exe)
    with pytest.raises(ValueError):
        manifest(str(link), revision, str(data), "read")


def test_probe_only_checks_local_fixture(release):
    _, _, _, data = release
    EventStore(data)
    probe(data, "read")
    with pytest.raises(OSError):
        probe(data, "execute")
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(str(data / "control.sock"))
        (data / "control.sock").chmod(0o660)
        probe(data, "execute")
        (data / "control.sock").chmod(0o666)
        with pytest.raises(ValueError):
            probe(data, "execute")

def test_writer_identity_and_release(release, monkeypatch):
    import os
    import pwd
    from types import SimpleNamespace
    from ocpp_csms import mcp_manifest as module
    _, exe, _, _ = release
    writer = exe.parent / "ocpp-csms"
    user = pwd.getpwuid(os.geteuid()).pw_name
    values = {"ExecStart": "{ path=" + str(writer) + " ; argv[]=... ; }", "User": user}
    monkeypatch.setattr(module.subprocess, "run", lambda argv, **kw: SimpleNamespace(stdout=values[argv[-2]]))
    module.verify_writer("ocpp-csms.service", exe)
    values["User"] = "not-the-service-user"
    with pytest.raises(ValueError, match="identity"):
        module.verify_writer("ocpp-csms.service", exe)
    values["User"] = user
    values["ExecStart"] = "{ path=/other/ocpp-csms ; }"
    with pytest.raises(ValueError, match="release"):
        module.verify_writer("ocpp-csms.service", exe)
