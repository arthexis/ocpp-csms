"""Isolated tests for socket-activated diagnose POST handler."""
from __future__ import annotations

import http.client
import threading
from http.server import HTTPServer

import pytest

from ocpp_csms import diagnose_receiver

TOKEN = "a" * 40


@pytest.fixture
def receiver(tmp_path, monkeypatch):
    root = tmp_path / "diagnostics"
    (root / TOKEN).mkdir(parents=True)
    monkeypatch.setattr(diagnose_receiver, "ROOT", root)
    server = HTTPServer(("127.0.0.1", 0), diagnose_receiver.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, root
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def submit(server, method, path, body=b"", content_type="application/octet-stream"):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    connection.request(method, path, body=body, headers={"Content-Type": content_type})
    response = connection.getresponse()
    status = response.status
    response.read()
    connection.close()
    return status


def test_abb_post_with_trailing_slash_stores_raw_file(receiver):
    server, root = receiver
    path = f"/ocpp-diagnose/{TOKEN}/upload.bin/"
    assert submit(server, "POST", path, b"ABB logs") == 201
    assert (root / TOKEN / "upload.bin").read_bytes() == b"ABB logs"
    assert submit(server, "POST", path, b"retry") == 409
    assert (root / TOKEN / "upload.bin").read_bytes() == b"ABB logs"


def test_post_without_trailing_slash_and_multipart(receiver):
    server, root = receiver
    boundary = "test-boundary"
    body = (b"--test-boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"logs.zip\"\r\n"
            b"Content-Type: application/zip\r\n\r\narchive bytes\r\n--test-boundary--\r\n")
    assert submit(server, "POST", f"/ocpp-diagnose/{TOKEN}/upload.bin", body,
                  f"multipart/form-data; boundary={boundary}") == 201
    assert (root / TOKEN / "upload.bin").read_bytes() == b"archive bytes"


def test_reject_unknown_tokens_get_and_oversized_request(receiver, monkeypatch):
    server, root = receiver
    assert submit(server, "POST", f"/ocpp-diagnose/{'b'*40}/upload.bin/", b"bad") == 404
    assert submit(server, "GET", f"/ocpp-diagnose/{TOKEN}/upload.bin") == 405
    monkeypatch.setattr(diagnose_receiver, "MAX_BODY", 2)
    assert submit(server, "POST", f"/ocpp-diagnose/{TOKEN}/upload.bin/", b"123") == 413
    assert not (root / TOKEN / "upload.bin").exists()


def test_contract_nginx_socket_activation_and_native_put():
    from pathlib import Path
    site = Path("ansible/roles/ocpp_expose/templates/ocpp-csms-site.conf.j2").read_text()
    tasks = Path("ansible/roles/ocpp_expose/tasks/main.yml").read_text()
    assert "location @ocpp_diagnose_post" in site
    assert "ocpp-csms-diagnose.sock" in site
    assert "dav_methods PUT;" in site
    assert "ocpp-csms-diagnose.service" in tasks
    assert "ocpp-csms-diagnose.socket" in tasks
