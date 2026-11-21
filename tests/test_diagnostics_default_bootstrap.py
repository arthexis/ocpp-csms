"""Default CSMS diagnostics bootstrap and offline-private invariants."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _auto_helper(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    path = ROOT / "scripts/private_diagnostics_auto.py"
    spec = importlib.util.spec_from_file_location("diag_auto_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_regular_deploy_provisions_unix_receiver_without_public_exposure():
    install = (ROOT / "ansible/playbooks/install.yml").read_text()
    base = (ROOT / "ansible/roles/ocpp_diagnostics_receiver/tasks/main.yml").read_text()
    field = (ROOT / "ansible/tasks/private-diagnostics.yml").read_text()
    public = (ROOT / "ansible/playbooks/expose.yml").read_text()
    private_wrapper = (ROOT / "ansible/playbooks/private-diagnostics.yml").read_text()
    assert "Provision diagnostics receiver for every normal installation" in install
    assert "name: ocpp_diagnostics_receiver" in install
    assert "name: ocpp_diagnostics_receiver" in field
    assert "private_diagnostics_auto.py" in install
    assert "failed_when: false" in install
    assert "when:" in install and "ocpp_eth0.stat.exists" in install
    assert "ocpp_private_diag_ready.rc | default(1) == 0" in install
    assert "ocpp_csms_stage_only" in install
    assert "nginx', 'openssl'" in install
    assert "ocpp_nginx_preinstalled" in install
    assert "ocpp-csms-diagnose.socket" in base
    assert "/run/ocpp-csms-diagnose.sock" in base
    assert "listen " not in base
    assert "ocpp_expose" not in install
    assert "name: ocpp_expose" in public
    assert "name: ocpp_diagnostics_receiver" in (ROOT / "ansible/roles/ocpp_expose/tasks/main.yml").read_text()
    assert "import_tasks: ../tasks/private-diagnostics.yml" in private_wrapper


@pytest.mark.parametrize("neighbor_rows,expected", [
    ([{"dst": "192.168.129.191", "lladdr": "aa:bb:cc:dd:ee:ff", "state": ["REACHABLE"]}],
     "192.168.129.191"),
    ([{"dst": "192.168.129.191", "lladdr": "aa:bb:cc:dd:ee:ff", "state": "STALE"}],
     "192.168.129.191"),
])
def test_unique_field_neighbor(monkeypatch, neighbor_rows, expected):
    auto = _auto_helper(monkeypatch)
    def output(cmd, **kwargs):
        if "addr" in cmd:
            return json.dumps([{"addr_info": [{"family": "inet", "local": "192.168.129.10",
                                              "prefixlen": 16}]}])
        return json.dumps(neighbor_rows)
    monkeypatch.setattr(auto.subprocess, "check_output", output)
    assert auto.unique_eth0_peer() == expected


@pytest.mark.parametrize("neighbor_rows", [
    [],
    [{"dst": "192.168.129.191", "state": ["FAILED"], "lladdr": "aa:bb:cc:dd:ee:ff"}],
    [{"dst": "192.168.129.191", "state": ["INCOMPLETE"]}],
    [{"dst": "192.168.129.191", "state": ["REACHABLE"], "lladdr": "aa:bb:cc:dd:ee:ff"},
     {"dst": "192.168.129.182", "state": ["REACHABLE"], "lladdr": "ee:bb:cc:dd:ee:ff"}],
])
def test_offline_or_ambiguous_neighbor_never_autopublishes(monkeypatch, neighbor_rows):
    auto = _auto_helper(monkeypatch)
    def output(cmd, **kwargs):
        if "addr" in cmd:
            return json.dumps([{"addr_info": [{"family": "inet", "local": "192.168.129.10",
                                              "prefixlen": 16}]}])
        return json.dumps(neighbor_rows)
    monkeypatch.setattr(auto.subprocess, "check_output", output)
    with pytest.raises(ValueError, match="expected exactly one charger"):
        auto.unique_eth0_peer()


def test_private_and_public_ingress_stay_independent():
    public = (ROOT / "ansible/roles/ocpp_expose/templates/ocpp-csms-site.conf.j2").read_text()
    private = (ROOT / "ansible/templates/private-diagnostics-nginx.conf.j2").read_text()
    assert "ssl_certificate /etc/letsencrypt/live/" in public
    assert "proxy_pass http://127.0.0.1:{{ ocpp_expose_upstream_port }}" in public
    assert "proxy_pass http://unix:/run/ocpp-csms-diagnose.sock:" in public
    assert "listen {{ diag_ip }}:{{ diag_port_num }} ssl;" in private
    assert "allow {{ diag_charger_ip }};" in private
    assert "proxy_pass http://127.0.0.1:9000" not in private
    assert "ocpp_expose" not in (ROOT / "ansible/playbooks/install.yml").read_text()


def test_explicit_no_probe_deploy_preserves_socket_without_getconfiguration():
    wrapper = (ROOT / "deploy.sh").read_text()
    install = (ROOT / "ansible/playbooks/install.yml").read_text()
    assert "--no-private-diag-probe) private_diag_probe=0" in wrapper
    assert "ocpp_csms_private_diag_probe=false" in wrapper
    assert 'if [ "$private_diag_probe" -eq 0 ]' in wrapper
    assert "Provision diagnostics receiver for every normal installation" in install

    boundaries = (
        ("Check whether a charger-reachable private endpoint is available",
         "Configure private diagnostics automatically for connected charger"),
        ("Configure private diagnostics automatically for connected charger",
         "Report deferred private HTTPS when no charger is connected"),
        ("Report deferred private HTTPS when no charger is connected",
         "Report explicitly skipped live private diagnostics probe"),
    )
    for start, stop in boundaries:
        scoped = install.split("- name: " + start, 1)[1].split("- name: " + stop, 1)[0]
        assert "- ocpp_csms_private_diag_probe | default(true) | bool" in scoped
    assert "- not (ocpp_csms_private_diag_probe | default(true) | bool)" in install


def test_no_probe_is_not_accepted_with_explicit_diagnostics(tmp_path):
    import os
    import subprocess
    if os.geteuid() == 0:
        pytest.skip("deploy wrapper must run unprivileged")
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        ["sh", str(ROOT / "deploy.sh"), "--diagnostics", "--no-private-diag-probe"],
        env={**os.environ, "HOME": str(home)}, cwd=ROOT,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert "Use --diagnostics" in result.stderr
