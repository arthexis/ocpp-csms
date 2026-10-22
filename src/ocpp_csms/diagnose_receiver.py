"""Socket-activated, loopback-only HTTP POST upload receiver for charger diagnostics.

nginx validates the public URL, terminates TLS and enforces the body limit.
This receiver independently validates paths, expiry, type, size and storage.
"""
from __future__ import annotations

import email.policy
import os
import re
import socket
from datetime import datetime, timezone
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from ocpp_csms.diagnostic_upload import ROOT, TOKEN_RE

MAX_BODY = 100 * 1024 * 1024
PATH = re.compile(r"^/ocpp-diagnose/([a-f0-9]{40})/upload\.bin/?$")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        # URL contains an authorization capability; never log HTTP paths.
        pass

    def reply(self, status: int):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_POST(self):
        match = PATH.fullmatch(urlsplit(self.path).path)
        if not match or urlsplit(self.path).query:
            return self.reply(404)
        length = self.headers.get("Content-Length")
        if length is None or not length.isdecimal():
            return self.reply(411)
        length = int(length)
        if length == 0 or length > MAX_BODY:
            return self.reply(413)
        token = match.group(1)
        folder = ROOT / token
        # Require the independent CLI to have prepared this exact directory.
        if not TOKEN_RE.fullmatch(token) or not folder.is_dir() or folder.is_symlink():
            return self.reply(404)
        content_type = self.headers.get("Content-Type", "application/octet-stream")
        if not (content_type.startswith("multipart/form-data;") or content_type.split(";", 1)[0].strip() in
                ("application/octet-stream", "application/zip", "application/x-gzip", "application/gzip", "application/x-tar")):
            return self.reply(415)
        # Never overwrite prior success, including concurrent retries; exclusive create.
        target = folder / "upload.bin"
        try:
            with target.open("xb") as stream:
                remaining = length
                while remaining:
                    data = self.rfile.read(min(65536, remaining))
                    if not data:
                        raise ConnectionError("incomplete body")
                    stream.write(data)
                    remaining -= len(data)
                stream.flush()
                os.fsync(stream.fileno())
            if content_type.startswith("multipart/form-data;"):
                # Parse the bounded MIME envelope and retain only one uploaded
                # file, without trusting the client-provided filename.
                raw = target.read_bytes()
                message = BytesParser(policy=email.policy.default).parsebytes(
                    b"Content-Type: " + content_type.encode("ascii", errors="ignore") +
                    b"\r\nMIME-Version: 1.0\r\n\r\n" + raw
                )
                parts = [part for part in message.iter_parts() if part.get_filename()]
                if not message.is_multipart() or len(parts) != 1:
                    target.unlink(missing_ok=True)
                    return self.reply(400)
                payload = parts[0].get_payload(decode=True)
                if not payload:
                    target.unlink(missing_ok=True)
                    return self.reply(400)
                with target.open("wb") as stream:
                    stream.write(payload)
            return self.reply(201)
        except FileExistsError:
            return self.reply(409)
        except (OSError, ConnectionError):
            target.unlink(missing_ok=True)
            return self.reply(500)

    def do_PUT(self):
        self.reply(405)

    def do_GET(self):
        self.reply(405)


def main():
    if int(os.environ.get("LISTEN_FDS", "0")) != 1 or int(os.environ.get("LISTEN_PID", "0")) != os.getpid():
        raise SystemExit("ocpp-csms-diagnose requires systemd socket activation")
    fd = socket.fromfd(3, socket.AF_UNIX, socket.SOCK_STREAM)
    server = HTTPServer(("localhost", 0), Handler, bind_and_activate=False)
    server.socket.close()
    server.socket = fd
    server.server_address = "/run/ocpp-csms-diagnose.sock"
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
