from types import SimpleNamespace

import pytest

from ocpp_discover import diagnosis, handoff, service
from ocpp_discover.redirect import RedirectReceipt, WebSocketRequest


def receipt(*, port=8080, source_ip="172.16.5.40"):
    return RedirectReceipt(interface="enp7s0", listen_port=9100, source_ip=source_ip, destination_ips=["172.16.5.1"], requests=[WebSocketRequest("172.16.5.1", f"172.16.5.1:{port}", "/ocpp/CP7")], captured_at="2026-10-05T00:00:00+00:00", destination_port=port)


def discovery_result(adaptation=None):
    adaptation = adaptation or receipt()
    return SimpleNamespace(charger_id="CP7", receipt=adaptation, to_json=lambda: {"status": "connected", "charger_id": "CP7"})


def forbid_discovery(monkeypatch):
    monkeypatch.setattr(service.discover, "run_discovery", lambda **kwargs: pytest.fail("persistent observation must not trigger first-time discovery"))


def configured(monkeypatch):
    monkeypatch.setattr(service.diagnosis, "inspect_configuration", lambda expected: diagnosis.Configuration(True, True))


def wake(monkeypatch, kind="expected"):
    monkeypatch.setattr(service.diagnosis, "wait_for_discovery_evidence", lambda *args, **kwargs: diagnosis.DiscoveryEvidence(kind, "packet"))


def test_service_waits_for_positive_evidence_before_first_discovery(tmp_path, monkeypatch):
    calls = []
    persistent = tmp_path / "persistent"
    monkeypatch.setattr(service.diagnosis, "wait_for_discovery_evidence", lambda interface, **kwargs: calls.append(("wait", interface)) or diagnosis.DiscoveryEvidence("candidate", "arp"))
    monkeypatch.setattr(service.discover, "run_discovery", lambda **kwargs: calls.append(("discover", kwargs)) or discovery_result())
    monkeypatch.setattr(service.handoff, "_promote_persistent_adaptation", lambda state, adaptation: calls.append(("promote", state, adaptation)))
    outcome = service.run_service(data_dir=tmp_path / "data", runtime_dir=tmp_path / "runtime", persistent_dir=persistent, interface="enp7s0", listen_port=9100, max_cycles=1)
    assert outcome["status"] == "discovered"
    assert calls[0] == ("wait", "enp7s0")
    assert calls[1][0] == "discover"
    assert calls[2][0] == "promote"


def test_service_proves_persistent_adaptation_from_fresh_connection_and_ocpp(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    handoff.persist_discovered(persistent, receipt())
    forbid_discovery(monkeypatch)
    calls = []
    monkeypatch.setattr(service, "connection_markers", lambda data_dir, expected: calls.append(("connection_baseline", expected)) or {"CP7": 4})
    monkeypatch.setattr(handoff, "_ocpp_markers", lambda data_dir, expected: calls.append(("ocpp_baseline", expected)) or {"CP7": 8})
    monkeypatch.setattr(service, "wait_for_reconnect", lambda data_dir, markers, timeout: calls.append(("connection", markers, timeout)) or ())
    monkeypatch.setattr(handoff, "wait_for_fresh_ocpp", lambda data_dir, markers, timeout: calls.append(("ocpp", markers, timeout)) or ())
    outcome = service.run_service(data_dir=tmp_path / "data", persistent_dir=persistent, boot_timeout=120, max_cycles=1)
    assert outcome == {"status": "persistent", "chargers": ["CP7"]}
    assert [call[0] for call in calls] == ["connection_baseline", "ocpp_baseline", "connection", "ocpp"]


def test_service_remains_resident_after_healthy_persistent_validation(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    handoff.persist_discovered(persistent, receipt())
    forbid_discovery(monkeypatch)
    configured(monkeypatch)
    monkeypatch.setattr(service, "_validate", lambda *args, **kwargs: ())
    calls = []

    def recovery(*args, **kwargs):
        calls.append("monitor")
        return {"status": "persistent", "chargers": ["CP7"]}

    monkeypatch.setattr(service, "_wait_for_recovery", recovery)
    outcome = service.run_service(data_dir=tmp_path / "data", persistent_dir=persistent, max_cycles=2)
    assert outcome == {"status": "persistent", "chargers": ["CP7"]}
    assert calls == ["monitor"]


def test_timeout_logs_once_then_passive_wake_is_revalidated(tmp_path, monkeypatch, caplog):
    persistent = tmp_path / "persistent"
    handoff.persist_discovered(persistent, receipt())
    forbid_discovery(monkeypatch)
    configured(monkeypatch)
    wake(monkeypatch)
    connection_baselines = iter(({"CP7": 1}, {"CP7": 1}))
    reconnect_results = iter((("CP7",), ()))
    monkeypatch.setattr(service, "connection_markers", lambda data_dir, expected: next(connection_baselines))
    monkeypatch.setattr(handoff, "_ocpp_markers", lambda data_dir, expected: {"CP7": 3})
    monkeypatch.setattr(service, "wait_for_reconnect", lambda data_dir, markers, timeout: next(reconnect_results))
    monkeypatch.setattr(handoff, "wait_for_fresh_ocpp", lambda *args, **kwargs: ())
    with caplog.at_level("ERROR"):
        outcome = service.run_service(data_dir=tmp_path / "data", persistent_dir=persistent, boot_timeout=180, max_cycles=1)
    assert outcome == {"status": "persistent", "chargers": ["CP7"]}
    errors = [record for record in caplog.records if record.levelname == "ERROR"]
    assert len(errors) == 1
    assert "persistent adaptation unchanged" in errors[0].message


def test_new_charger_activity_can_replace_previous_adaptation(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    old = receipt(port=8080, source_ip="172.16.5.40")
    candidate = receipt(port=9999, source_ip="172.16.5.99")
    handoff.persist_discovered(persistent, old)
    forbid_discovery(monkeypatch)
    configured(monkeypatch)
    wake(monkeypatch, "candidate")
    monkeypatch.setattr(service, "connection_markers", lambda data_dir, expected: {"CP7": 0})
    monkeypatch.setattr(handoff, "_ocpp_markers", lambda data_dir, expected: {"CP7": 0})
    monkeypatch.setattr(service, "wait_for_reconnect", lambda data_dir, markers, timeout: ("CP7",))
    monkeypatch.setattr(service.diagnosis, "observe_endpoint", lambda *args, **kwargs: candidate)
    calls = []
    monkeypatch.setattr(service.reconcile, "reconcile", lambda **kwargs: calls.append(kwargs) or candidate)
    outcome = service.run_service(data_dir=tmp_path / "data", persistent_dir=persistent, boot_timeout=0, max_cycles=1)
    assert outcome == {"status": "reconciled", "chargers": ["CP7"]}
    assert calls[0]["expected"] == old
    assert calls[0]["candidate"] == candidate


def test_configuration_mismatch_does_not_mutate_without_endpoint_evidence(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    handoff.persist_discovered(persistent, receipt())
    forbid_discovery(monkeypatch)
    monkeypatch.setattr(service.diagnosis, "inspect_configuration", lambda expected: diagnosis.Configuration(False, False))
    wake(monkeypatch)
    monkeypatch.setattr(service, "connection_markers", lambda data_dir, expected: {"CP7": 0})
    monkeypatch.setattr(handoff, "_ocpp_markers", lambda data_dir, expected: {"CP7": 0})
    reconnect_results = iter((("CP7",), ()))
    monkeypatch.setattr(service, "wait_for_reconnect", lambda data_dir, markers, timeout: next(reconnect_results))
    monkeypatch.setattr(handoff, "wait_for_fresh_ocpp", lambda *args, **kwargs: ())
    monkeypatch.setattr(service.reconcile, "reconcile", lambda **kwargs: pytest.fail("configuration mismatch alone must not authorize mutation"))
    outcome = service.run_service(data_dir=tmp_path / "data", persistent_dir=persistent, boot_timeout=0, max_cycles=1)
    assert outcome["status"] == "persistent"


def test_service_fails_closed_when_discovered_receipt_is_invalid(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    persistent.mkdir()
    handoff.discovered_path(persistent).write_text("malformed\n")
    monkeypatch.setattr(service.discover, "run_discovery", lambda **kwargs: pytest.fail("invalid durable state must not fall through to discovery"))
    with pytest.raises(RuntimeError, match="invalid_discovered_receipt"):
        service.run_service(data_dir=tmp_path / "data", runtime_dir=tmp_path / "runtime", persistent_dir=persistent, listen_port=9100, max_cycles=1)
