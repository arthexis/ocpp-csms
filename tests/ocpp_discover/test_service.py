from types import SimpleNamespace

import pytest

from ocpp_discover import service


def test_service_runs_discovery_when_durable_adaptation_is_absent(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        service.discover,
        "run_discovery",
        lambda **kwargs: calls.append(("discover", kwargs)) or SimpleNamespace(to_json=lambda: {"status": "connected"}),
    )
    monkeypatch.setattr(
        service.restore,
        "restore_path_a",
        lambda **kwargs: pytest.fail("restore must not run without durable state"),
    )

    outcome = service.run_service(
        data_dir=tmp_path / "data",
        runtime_dir=tmp_path / "runtime",
        persistent_dir=tmp_path / "persistent",
        interface="enp7s0",
        listen_port=9100,
    )

    assert outcome["status"] == "discovered"
    assert calls[0][0] == "discover"
    assert calls[0][1]["interface"] == "enp7s0"
    assert calls[0][1]["listen_port"] == 9100


def test_service_restores_when_durable_adaptation_exists(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    persistent.mkdir()
    service.handoff.persistent_receipt_path(persistent).write_text("present\n")
    calls = []
    monkeypatch.setattr(
        service.restore,
        "restore_path_a",
        lambda **kwargs: calls.append(("restore", kwargs)) or "table ip ocpp_field_redirect {}\n",
    )
    monkeypatch.setattr(
        service.discover,
        "run_discovery",
        lambda **kwargs: pytest.fail("discovery must not run when durable state exists"),
    )

    outcome = service.run_service(
        data_dir=tmp_path / "data",
        runtime_dir=tmp_path / "runtime",
        persistent_dir=persistent,
        interface="enp7s0",
        listen_port=9100,
    )

    assert outcome["status"] == "restored"
    assert calls == [
        (
            "restore",
            {
                "persistent_dir": persistent,
                "runtime_dir": tmp_path / "runtime",
                "listen_port": 9100,
            },
        )
    ]


def test_service_fails_closed_when_existing_durable_state_is_invalid(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    persistent.mkdir()
    service.handoff.persistent_receipt_path(persistent).write_text("malformed\n")
    monkeypatch.setattr(
        service.restore,
        "restore_path_a",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("invalid_persistent_path_a_receipt")),
    )
    monkeypatch.setattr(
        service.discover,
        "run_discovery",
        lambda **kwargs: pytest.fail("invalid durable state must not fall through to discovery"),
    )

    with pytest.raises(RuntimeError, match="invalid_persistent_path_a_receipt"):
        service.run_service(
            data_dir=tmp_path / "data",
            runtime_dir=tmp_path / "runtime",
            persistent_dir=persistent,
            listen_port=9100,
        )


def test_service_does_not_fallback_when_restore_environment_is_incompatible(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    persistent.mkdir()
    service.handoff.persistent_receipt_path(persistent).write_text("present\n")
    monkeypatch.setattr(
        service.restore,
        "restore_path_a",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("persistent_path_a_listener_mismatch")),
    )
    monkeypatch.setattr(
        service.discover,
        "run_discovery",
        lambda **kwargs: pytest.fail("restore failures must never trigger discovery fallback"),
    )

    with pytest.raises(RuntimeError, match="persistent_path_a_listener_mismatch"):
        service.run_service(
            data_dir=tmp_path / "data",
            runtime_dir=tmp_path / "runtime",
            persistent_dir=persistent,
            listen_port=9200,
        )
