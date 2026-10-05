from types import SimpleNamespace

import pytest

from ocpp_discover import handoff, service
from ocpp_discover.redirect import RedirectReceipt, WebSocketRequest


def receipt(*, port=8080):
    return RedirectReceipt(interface="enp7s0", listen_port=9100, source_ip="172.16.5.40", destination_ips=["172.16.5.1"], requests=[WebSocketRequest("172.16.5.1", f"172.16.5.1:{port}", "/ocpp/CP7")], captured_at="2026-10-05T00:00:00+00:00", destination_port=port)


def forbid_discovery(monkeypatch):
    monkeypatch.setattr(service.discover, "run_discovery", lambda **kwargs: pytest.fail("persistent observation must not trigger first-time discovery"))


def configured(monkeypatch):
    monkeypatch.setattr(service.diagnosis, "inspect_configuration", lambda expected: SimpleNamespace(configured_matches=True, live_table_present=True))


def test_service_runs_discovery_when_durable_adaptation_is_absent(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(service.discover, "run_discovery", lambda **kwargs: calls.append(("discover", kwargs)) or SimpleNamespace(to_json=lambda: {"status": "connected"}))
    outcome = service.run_service(data_dir=tmp_path / "data", runtime_dir=tmp_path / "runtime", persistent_dir=tmp_path / "persistent", interface="enp7s0", listen_port=9100)
    assert outcome["status"] == "discovered"
    assert calls[0][1]["interface"] == "enp7s0"


def test_service_proves_persistent_adaptation_from_fresh_connection_and_ocpp(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    handoff.persist_discovered(persistent, receipt())
    forbid_discovery(monkeypatch)
    calls = []
    monkeypatch.setattr(service, "connection_markers", lambda data_dir, expected: calls.append(("connection_baseline", expected)) or {"CP7": 4})
    monkeypatch.setattr(handoff, "_ocpp_markers", lambda data_dir, expected: calls.append(("ocpp_baseline", expected)) or {"CP7": 8})
    monkeypatch.setattr(service, "wait_for_reconnect", lambda data_dir, markers, timeout: calls.append(("connection", markers, timeout)) or ())
    monkeypatch.setattr(handoff, "wait_for_fresh_ocpp", lambda data_dir, markers, timeout: calls.append(("ocpp", markers, timeout)) or ())

    outcome = service.run_service(data_dir=tmp_path / "data", persistent_dir=persistent, boot_timeout=120)

    assert outcome == {"status": "persistent", "chargers": ["CP7"]}
    assert [call[0] for call in calls] == ["connection_baseline", "ocpp_baseline", "connection", "ocpp"]
    assert handoff.load_discovered(persistent) == receipt()


def test_timeout_logs_once_then_late_connection_is_revalidated(tmp_path, monkeypatch, caplog):
    persistent = tmp_path / "persistent"
    handoff.persist_discovered(persistent, receipt())
    forbid_discovery(monkeypatch)
    configured(monkeypatch)
    connection_baselines = iter(({"CP7": 1}, {"CP7": 1}))
    reconnect_results = iter((("CP7",), ()))
    monkeypatch.setattr(service, "connection_markers", lambda data_dir, expected: next(connection_baselines))
    monkeypatch.setattr(handoff, "_ocpp_markers", lambda data_dir, expected: {"CP7": 3})
    monkeypatch.setattr(service, "wait_for_reconnect", lambda data_dir, markers, timeout: next(reconnect_results))
    monkeypatch.setattr(service.diagnosis, "observe_contradiction", lambda expected, seconds: None)
    monkeypatch.setattr(handoff, "wait_for_fresh_ocpp", lambda *args, **kwargs: ())

    with caplog.at_level("ERROR"):
        outcome = service.run_service(data_dir=tmp_path / "data", persistent_dir=persistent, boot_timeout=180, wait_interval=5)

    assert outcome == {"status": "persistent", "chargers": ["CP7"]}
    errors = [record for record in caplog.records if record.levelname == "ERROR"]
    assert len(errors) == 1
    assert "persistent adaptation unchanged" in errors[0].message


def test_positive_contradiction_enters_safe_reconciliation(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    old = receipt(port=8080)
    candidate = receipt(port=9999)
    handoff.persist_discovered(persistent, old)
    forbid_discovery(monkeypatch)
    configured(monkeypatch)
    monkeypatch.setattr(service, "connection_markers", lambda data_dir, expected: {"CP7": 0})
    monkeypatch.setattr(handoff, "_ocpp_markers", lambda data_dir, expected: {"CP7": 0})
    monkeypatch.setattr(service, "wait_for_reconnect", lambda data_dir, markers, timeout: ("CP7",))
    monkeypatch.setattr(service.diagnosis, "observe_contradiction", lambda expected, seconds: candidate)
    calls = []
    monkeypatch.setattr(service.reconcile, "reconcile", lambda **kwargs: calls.append(kwargs) or candidate)

    outcome = service.run_service(data_dir=tmp_path / "data", persistent_dir=persistent, boot_timeout=0, wait_interval=1)

    assert outcome == {"status": "reconciled", "chargers": ["CP7"]}
    assert calls[0]["expected"] == old
    assert calls[0]["candidate"] == candidate


def test_configuration_mismatch_is_logged_but_does_not_mutate_without_endpoint_evidence(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    handoff.persist_discovered(persistent, receipt())
    forbid_discovery(monkeypatch)
    monkeypatch.setattr(service.diagnosis, "inspect_configuration", lambda expected: SimpleNamespace(configured_matches=False, live_table_present=False))
    monkeypatch.setattr(service, "connection_markers", lambda data_dir, expected: {"CP7": 0})
    monkeypatch.setattr(handoff, "_ocpp_markers", lambda data_dir, expected: {"CP7": 0})
    reconnect_results = iter((("CP7",), ()))
    monkeypatch.setattr(service, "wait_for_reconnect", lambda data_dir, markers, timeout: next(reconnect_results))
    monkeypatch.setattr(service.diagnosis, "observe_contradiction", lambda expected, seconds: None)
    monkeypatch.setattr(handoff, "wait_for_fresh_ocpp", lambda *args, **kwargs: ())
    monkeypatch.setattr(service.reconcile, "reconcile", lambda **kwargs: pytest.fail("configuration mismatch alone must not authorize mutation"))

    outcome = service.run_service(data_dir=tmp_path / "data", persistent_dir=persistent, boot_timeout=0, wait_interval=1)

    assert outcome["status"] == "persistent"


def test_service_fails_closed_when_discovered_receipt_is_invalid(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    persistent.mkdir()
    handoff.discovered_path(persistent).write_text("malformed\n")
    monkeypatch.setattr(service.discover, "run_discovery", lambda **kwargs: pytest.fail("invalid durable state must not fall through to discovery"))
    with pytest.raises(RuntimeError, match="invalid_discovered_receipt"):
        service.run_service(data_dir=tmp_path / "data", runtime_dir=tmp_path / "runtime", persistent_dir=persistent, listen_port=9100)
