"""HTTPS diagnostic upload request lifecycle and CLI parser regression."""
from __future__ import annotations

import json

from ocpp_csms.cli import build_parser
from ocpp_csms import diagnostic_upload


def test_diagnose_subcommands_parse_without_legacy_upload_url():
    parser, _ = build_parser()
    args = parser.parse_args(["diagnose", "request", "--cp", "CP001"])
    assert args.command == "diagnose"
    assert args.diagnostic_operation == "request"
    assert args.charger == "CP001"
    assert parser.parse_args(["diagnose", "status"]).diagnostic_operation == "status"
    assert parser.parse_args(["diagnose", "list"]).diagnostic_operation == "list"
    assert parser.parse_args(["diagnose", "show"]).diagnostic_operation == "show"


def test_request_prepares_destination_and_indexes_received_file(tmp_path, monkeypatch):
    root = tmp_path / "incoming"
    root.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"hostname": "charger.example.com"}))
    monkeypatch.setattr(diagnostic_upload, "ROOT", root)
    monkeypatch.setattr(diagnostic_upload, "CONFIG", config)
    record, location = diagnostic_upload.prepare(tmp_path, "CP001")
    assert location.startswith("https://charger.example.com/ocpp-diagnose/")
    assert location.endswith("/upload.bin")
    assert record["charger"] == "CP001"
    diagnostic_upload.record_response(tmp_path, record["id"], {"file_name": "diag.zip"})
    target = root / record["id"] / "upload.bin"
    target.write_bytes(b"diagnostics")
    found = diagnostic_upload.list_requests(tmp_path, charger="CP001")
    assert len(found) == 1
    assert found[0]["result"] == "received"
    assert found[0]["suggested_file"] == "diag.zip"
    assert found[0]["files"] == [{"name": "upload.bin", "bytes": 11}]
    assert diagnostic_upload.list_requests(tmp_path, charger="different") == []


def test_request_remains_pending_without_archive(tmp_path, monkeypatch):
    root = tmp_path / "incoming"
    root.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"hostname": "charger.example.com"}))
    monkeypatch.setattr(diagnostic_upload, "ROOT", root)
    monkeypatch.setattr(diagnostic_upload, "CONFIG", config)
    record, _ = diagnostic_upload.prepare(tmp_path, None)
    assert diagnostic_upload.list_requests(tmp_path)[0]["result"] == "requested"
    assert (root / record["id"]).is_dir()


def test_safe_archive_inspection_reports_fault_without_extracting(tmp_path):
    import zipfile
    archive = tmp_path / "diag.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../outside.log", "INFO starting\\nERROR ABB converter alarm\\n")
        z.writestr("binary.bin", b"\\x00\\x01\\x02")
    lines = diagnostic_upload.inspect_file(archive)
    assert any("ABB converter alarm" in line for line in lines)
    assert not (tmp_path.parent / "outside.log").exists()

def test_private_diagnostics_port_in_url(tmp_path, monkeypatch):
    import pytest
    root = tmp_path / "incoming"
    root.mkdir()
    config = tmp_path / "config.json"
    monkeypatch.setattr(diagnostic_upload, "ROOT", root)
    monkeypatch.setattr(diagnostic_upload, "CONFIG", config)
    config.write_text(json.dumps({"hostname": "192.168.129.10", "port": 8443}))
    _, url = diagnostic_upload.prepare(tmp_path, "CP001")
    assert url.startswith("https://192.168.129.10:8443/ocpp-diagnose/")
    for invalid in (-1, 0, 65536, "8443", True):
        config.write_text(json.dumps({"hostname": "192.168.129.10", "port": invalid}))
        with pytest.raises(ValueError, match="port"):
            diagnostic_upload.configuration()


def test_diagnostics_private_deploy_one_flag_and_no_public_proxy():
    from pathlib import Path
    deploy = Path("deploy.sh").read_text()
    playbook = Path("ansible/tasks/private-diagnostics.yml").read_text()
    nginx = Path("ansible/templates/private-diagnostics-nginx.conf.j2").read_text()
    assert "--diagnostics)" in deploy
    assert 'private-diagnostics.yml' in deploy
    assert "expected exactly one charger on eth0" in playbook
    assert "listen {{ diag_ip }}:{{ diag_port_num }} ssl;" in nginx
    assert "allow {{ diag_charger_ip }};" in nginx
    assert "location / { return 404; }" in nginx
    assert "proxy_method POST;" in nginx
    assert 'proxy_pass http://127.0.0.1:9000' not in nginx
    assert 'upload\\.bin(?:/' in nginx

def test_private_port_selection_auto_reuse_and_explicit_override(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    import pytest
    path = Path("scripts/private_diagnostics_port.py")
    spec = importlib.util.spec_from_file_location("diag_port", path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    site = tmp_path / "site.conf"
    monkeypatch.setattr(helper, "DEFAULT_PORTS", (8443, 9443, 10443))
    monkeypatch.setattr(helper, "port_is_available", lambda ip, port: port != 8443)
    assert helper.choose("192.168.129.10", site) == 9443
    assert helper.choose("192.168.129.10", site, 10443) == 10443
    with pytest.raises(ValueError, match="occupied"):
        helper.choose("192.168.129.10", site, 8443)
    site.write_text("# Managed by ocpp-csms private diagnostics\\nserver { listen 192.168.129.10:9443 ssl; }")
    assert helper.choose("192.168.129.10", site) == 9443
    assert helper.choose("192.168.129.10", site, 9443) == 9443
    assert helper.choose("192.168.129.10", site, 10443) == 10443
    with pytest.raises(ValueError, match="range"):
        helper.choose("192.168.129.10", site, 442)
    with pytest.raises(ValueError, match="IPv4"):
        helper.choose("::1", site)


def test_private_port_selection_exhaustion_and_existing_only(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    import pytest
    spec = importlib.util.spec_from_file_location("diag_port_b", Path("scripts/private_diagnostics_port.py"))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    site = tmp_path / "site.conf"
    monkeypatch.setattr(helper, "port_is_available", lambda ip, port: False)
    with pytest.raises(ValueError, match="no private HTTPS port available"):
        helper.choose("192.168.129.10", site)
    site.write_text("server { listen 192.168.129.10:8443 ssl; }")
    with pytest.raises(ValueError, match="no private HTTPS port available"):
        helper.choose("192.168.129.10", site)
