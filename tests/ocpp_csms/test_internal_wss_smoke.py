"""Internal WSS smoke helper contracts; no live network or credentials required."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/internal_wss_smoke.py"


def test_internal_wss_smoke_script_compiles():
    ast.parse(SCRIPT.read_text(encoding="utf-8"))


def test_wss_smoke_stays_on_loopback_and_verifies_tls():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'host="127.0.0.1"' in source
    assert 'socket.create_connection(("127.0.0.1", args.port)' in source
    assert "ssl.create_default_context(cafile=str(ca_file))" in source
    assert "wss://" in source
    assert 'subprotocols=["ocpp1.6"]' in source
    assert "getpass.getpass" in source
    assert "StartTransaction" in source and "StopTransaction" in source
    assert "sqlite3.connect" in source
    assert "server_hostname" not in source  # keep hostname from URI for certificate verification


def test_wss_smoke_does_not_modify_service_or_authentication_configuration():
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("systemctl", "certbot", "openssl req", "auth basic", "subprocess.run",
                      "write_config", "claim_basic", "create_basic"):
        assert forbidden not in source
