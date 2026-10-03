from __future__ import annotations

import pytest

import ocpp_csms.app as app
from ocpp_csms.app import build_parser


class ProfileControlStub:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.response: dict[str, object] = {"ok": True, "response": {"status": "Accepted"}}

    async def __call__(self, data_dir, request):
        self.calls.append((data_dir, request))
        return self.response


@pytest.fixture
def cli_parser():
    parser, _ = build_parser()
    return parser


@pytest.fixture
def profile_control(monkeypatch):
    stub = ProfileControlStub()
    monkeypatch.setattr(app, "send_control", stub)
    return stub
