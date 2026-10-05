import json
from types import SimpleNamespace

import pytest

from ocpp_discover import handoff, service
from ocpp_discover.redirect import RedirectReceipt, WebSocketRequest


def receipt():
    return RedirectReceipt(
        interface="enp7s0",
        listen_port=9100,
        source_ip="172.16.5.40",
        destination_ips=["172.16.5.1"],
        requests=[WebSocketRequest("172.16.5.1", "172.16.5.1:8080", "/ocpp/CP7")],
        captured_at="2026-10-05T00:00:00+00:00",
        destination_port=8080,
    )


def test_service_runs_discovery_when_durable_adaptation_is_absent(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        service.discover,
        "run_discovery",
        lambda **kwargs: calls.append(("discover", kwargs)) or SimpleNamespace(to_json=lambda: {"status": "connected"}),
    )

    outcome = service.run_service(
        data_dir=tmp_path / "data",
        runtime_dir=tmp_path / "runtime",
        persistent_dir=tmp_path / "persistent",
        interface="enp7s0",
        listen_port=9100,
    )

    assert outcome["status"] == "discovered"
    assert calls[0][1]["interface"] == "enp7s0"
    assert calls[0][1]["listen_port"] == 9100


def test_service_leaves_valid_persistent_adaptation_alone(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    handoff.persist_validated_path_a(persistent, receipt())
    monkeypatch.setattr(
        service.discover,
        "run_discovery",
        lambda **kwargs: pytest.fail("known persistent adaptation must not trigger discovery"),
    )

    outcome = service.run_service(
        data_dir=tmp_path / "data",
        runtime_dir=tmp_path / "runtime",
        persistent_dir=persistent,
        interface="enp7s0",
        listen_port=9100,
    )

    assert outcome == {"status": "persistent"}


def test_service_fails_closed_when_discovered_receipt_is_invalid(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    persistent.mkdir()
    handoff.persistent_receipt_path(persistent).write_text("malformed\n")
    monkeypatch.setattr(
        service.discover,
        "run_discovery",
        lambda **kwargs: pytest.fail("invalid durable state must not fall through to discovery"),
    )

    with pytest.raises(RuntimeError, match="invalid_discovered_receipt"):
        service.run_service(
            data_dir=tmp_path / "data",
            runtime_dir=tmp_path / "runtime",
            persistent_dir=persistent,
            listen_port=9100,
        )
