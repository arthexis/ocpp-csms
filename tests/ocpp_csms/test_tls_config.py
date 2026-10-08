from __future__ import annotations

import json
from argparse import Namespace

import pytest

from ocpp_csms import tls_config
from ocpp_csms.cli import build_parser
from ocpp_csms.cli.tls import run_tls


def test_tls_cli_parser_accepts_short_config_name():
    parser, commands = build_parser()
    assert "tls" in commands
    args = parser.parse_args(["tls", "config", "--cert", "/tmp/cert.pem", "--key", "/tmp/key.pem", "--hostname", "charger.example.test"])
    assert args.tls_command == "config"


def test_configuration_roundtrip_and_preserved_disabled_state(tmp_path):
    path = tmp_path / "tls.json"
    config = tls_config.TLSConfig("/tmp/cert.pem", "/tmp/key.pem", "charger.example.test", 9443)
    tls_config.write_config(config, path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert tls_config.read_config(path) == config
    assert tls_config.status(path)["enabled"] is False
    args = Namespace(tls_command="config", config_path=str(path), cert="/tmp/new.crt", key="/tmp/new.key", hostname="new.example.test", port=9444)
    assert run_tls(args) == 0
    assert tls_config.read_config(path).cert == "/tmp/new.crt"


def test_missing_config_and_invalid_cert_are_safe(tmp_path):
    path = tmp_path / "tls.json"
    assert tls_config.status(path)["configured"] is False
    config = tls_config.TLSConfig("/missing/cert", "/missing/key", "charger.example.test")
    result = tls_config.check_config(config)
    assert result["ready"] is False
    assert "cert_not_found" in result["errors"]
    assert "key_not_found" in result["errors"]
    assert result["listener"] == "not_implemented"


def test_invalid_configuration_rejected_without_changing_file(tmp_path):
    path = tmp_path / "tls.json"
    original = tls_config.TLSConfig("/tmp/cert", "/tmp/key", "valid.example")
    tls_config.write_config(original, path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="invalid_tls_port"):
        tls_config.write_config(tls_config.TLSConfig("/tmp/cert", "/tmp/key", "valid.example", 0), path)
    assert path.read_bytes() == before
    path.write_text(json.dumps({"cert": "/tmp/cert"}))
    with pytest.raises(ValueError, match="invalid_tls_configuration"):
        tls_config.read_config(path)


def test_tls_port_conflict_is_reported(tmp_path):
    cert = tmp_path / "cert"
    key = tmp_path / "key"
    cert.write_text("not a certificate")
    key.write_text("not a key")
    result = tls_config.check_config(tls_config.TLSConfig(str(cert), str(key), "host.test", 9000))
    assert not result["ready"]
    assert "tls_port_conflicts_with_ws" in result["errors"]
