"""Hermetic tests of charger-specific private diagnostics address discovery."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_charger_reachable_address_is_locally_owned():
    helper = _load("target_helper", "scripts/private_diagnostics_target.py")
    owned = ["192.168.129.10", "10.42.0.1", "192.168.1.82"]
    assert helper.choose_server_ip("10.42.0.1", owned) == "10.42.0.1"
    assert helper.choose_server_ip("192.168.129.10", owned) == "192.168.129.10"
    for invalid in ("8.8.8.8", "192.168.129.191", "0.0.0.0", "127.0.0.1",
                    "not-local.example", "224.0.0.1"):
        with pytest.raises(ValueError):
            helper.choose_server_ip(invalid, owned)


def test_parse_charger_sdomain_and_service_data_dir(tmp_path):
    helper = _load("target_proc", "scripts/private_diagnostics_target.py")
    assert helper.parse_domain_config("KEY\tACCESS\tVALUE\nsDomain\tRW\t10.42.0.1\n") == "10.42.0.1"
    with pytest.raises(ValueError):
        helper.parse_domain_config("KEY\tACCESS\tVALUE\n")
    with pytest.raises(ValueError):
        helper.parse_domain_config("sDomain\tRW\t10.42.0.1\nsDomain\tRW\t10.42.0.2\n")
    d = tmp_path / "999"
    d.mkdir()
    (d / "cmdline").write_bytes(b"/usr/bin/python3\0ocpp-csms\0--data-dir\0/var/schemas/8\0serve\0")
    assert helper.service_data_dir(999, tmp_path) == "/var/schemas/8"
    (d / "cmdline").write_bytes(b"ocpp-csms\0--data-dir=/var/schemas/9\0serve\0")
    assert helper.service_data_dir(999, tmp_path) == "/var/schemas/9"
    with pytest.raises(ValueError):
        helper.service_data_dir(0, tmp_path)


@pytest.mark.parametrize(("sdomain", "expected"), [
    ("10.42.0.1", "10.42.0.1"),
    ("192.168.129.10", "192.168.129.10"),
    ("192.168.1.82", None),
])
def test_discover_from_one_connected_charger(tmp_path, monkeypatch, sdomain, expected):
    helper = _load("target_discovery_" + sdomain.replace(".", "_"), "scripts/private_diagnostics_target.py")
    d = tmp_path / "555"
    d.mkdir()
    (d / "cmdline").write_bytes(b"ocpp-csms\0--data-dir\0/home/arthe/ocpp-csms-data/schemas/8\0serve\0")
    calls = []
    def fake_output(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "systemctl":
            return "555\n"
        if argv[0] == "ip":
            return json.dumps([
                {"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.129.10"}]},
                {"ifname": "wlan0", "addr_info": [{"family": "inet", "local": "10.42.0.1"}]},
                {"ifname": "wlan1", "addr_info": [{"family": "inet", "local": "192.168.1.82"}]},
                {"ifname": "gway", "addr_info": [{"family": "inet", "local": "10.90.0.5"}]},
            ])
        if "status" in argv:
            return json.dumps({"data": {"chargers": [{"id": "CP1", "connected": True}]}})
        if "config" in argv:
            return "KEY\tACCESS\tVALUE\nsDomain\tRW\t" + sdomain + "\n"
        raise AssertionError(argv)
    monkeypatch.setattr(helper.subprocess, "check_output", fake_output)
    if expected is None:
        with pytest.raises(ValueError, match="not assigned locally"):
            helper.discover("/opt/ocpp-csms", "192.168.129.182", proc_root=tmp_path)
    else:
        got = helper.discover("/opt/ocpp-csms", "192.168.129.182", proc_root=tmp_path)
        assert got == {"server_ip": expected, "charger_ip": "192.168.129.182", "charge_point": "CP1"}
    assert any("sDomain" in a for a in calls)


def test_port_is_retained_when_server_address_changes(tmp_path, monkeypatch):
    helper = _load("port_move", "scripts/private_diagnostics_port.py")
    site = tmp_path / "site.conf"
    site.write_text("# Managed by ocpp-csms private diagnostics\n"
                    "server {\n listen 192.168.129.10:8443 ssl;\n}\n")
    monkeypatch.setattr(helper, "port_is_available", lambda ip, port: True)
    assert helper.choose("10.42.0.1", site) == 8443
    assert helper.choose("192.168.129.10", site) == 8443
    monkeypatch.setattr(helper, "port_is_available", lambda ip, port: port != 8443)
    assert helper.choose("10.42.0.1", site) == 9443
    with pytest.raises(ValueError, match="occupied"):
        helper.choose("10.42.0.1", site, 8443)
