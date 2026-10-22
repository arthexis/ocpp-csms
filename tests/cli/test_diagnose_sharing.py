"""Download grants never expose upload tokens or cross CP boundaries."""
import json
import os
from types import SimpleNamespace

import pytest

from ocpp_csms.cli import build_parser
from ocpp_csms import diagnose_sharing as sharing


def test_diagnose_serve_cli_operations():
    parser, _ = build_parser()
    base = ["diagnose", "serve"]
    assert parser.parse_args(base).diagnostic_operation == "serve"
    assert parser.parse_args(base + ["--cp", "CP1", "--status"]).status is True
    assert parser.parse_args(base + ["--revoke"]).revoke is True
    with pytest.raises(SystemExit):
        parser.parse_args(base + ["--status", "--revoke"])
    with pytest.raises(SystemExit):
        parser.parse_args(base + ["--limit", "24h"])


def test_diagnose_serve_revoke_scoped_and_token_free(monkeypatch, tmp_path):
    records = tmp_path / "records" / "diagnostics-requests"
    records.mkdir(parents=True)
    uploads = tmp_path / "uploads"
    snippets = tmp_path / "nginx"
    auth = tmp_path / "auth"
    published = tmp_path / "downloads"
    for directory in (uploads, snippets, auth, published):
        directory.mkdir()
    for cp, token in (("CP1", "a" * 40), ("CP2", "b" * 40)):
        (records / (token + ".json")).write_text(json.dumps({"charger": cp}))
        (uploads / token).mkdir()
        (uploads / token / "upload.bin").write_bytes(b"PK\\x03\\x04archive")
    monkeypatch.setattr(sharing, "ROOT", uploads)
    monkeypatch.setattr(sharing, "NGINX_SNIPPETS", snippets)
    monkeypatch.setattr(sharing, "AUTH_DIR", auth)
    monkeypatch.setattr(sharing, "PUBLIC_ROOT", published)
    monkeypatch.setattr(sharing, "hostname", lambda: "diagnose.example.org")
    monkeypatch.setattr(sharing.os, "geteuid", lambda: 0)
    monkeypatch.setattr(sharing.os, "chown", lambda *args: None)
    monkeypatch.setattr(sharing.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=os.getgid()))
    monkeypatch.setattr(sharing, "_run", lambda *args, **kwargs: "$6$hashed")
    monkeypatch.setattr(sharing, "_reload", lambda: None)

    first = sharing.serve(tmp_path / "records", "CP1")
    assert first["files"] == 1
    assert first["password"]
    assert first["url"] == "https://diagnose.example.org/CP1/"
    assert len(list((published / "CP1").iterdir())) == 1
    assert not (published / "CP2").exists()
    assert "a" * 40 not in (snippets / "CP1.conf").read_text()
    assert "CP1:$6$hashed" in (auth / "CP1.htpasswd").read_text()
    assert sharing.status("CP1")["enabled"]

    sharing.serve(tmp_path / "records", "CP2")
    sharing.revoke("CP1")
    assert not sharing.status("CP1")["enabled"]
    assert sharing.status("CP2")["enabled"]
    assert not (published / "CP1").exists()
    assert (published / "CP2").is_dir()
    assert (uploads / ("a" * 40) / "upload.bin").is_file()


def test_diagnose_serve_rejects_invalid_cp():
    for value in ("../etc", "CP/other", "x y", ""):
        with pytest.raises(ValueError, match="invalid charge point"):
            sharing._cp(value)
