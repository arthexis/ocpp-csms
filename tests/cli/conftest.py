from __future__ import annotations

import pytest

import ocpp_csms.cli.profile as profile_cli
from ocpp_csms.cli import build_parser


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
def parse_cli(cli_parser):
    def parse(*args: str):
        return cli_parser.parse_args(list(args))

    return parse


@pytest.fixture
def profile_control(monkeypatch):
    stub = ProfileControlStub()
    monkeypatch.setattr(profile_cli, "send_control", stub)
    return stub
