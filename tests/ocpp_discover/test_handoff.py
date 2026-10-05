import json
from types import SimpleNamespace

import pytest

from field import handoff
from field.redirect import RedirectReceipt, WebSocketRequest


def receipt():
    return RedirectReceipt(interface="eth0", listen_port=9000, source_ip="192.168.129.182", destination_ips=["10.42.0.1"], requests=[WebSocketRequest("10.42.0.1", "10.42.0.1:8888", "/ocpp/CP1")], captured_at="discovered", destination_port=8888)


def seed_receipt(tmp_path):
    (tmp_path / "handoff-endpoint.json").write_text(json.dumps(receipt().to_json()))


def allowed(*chargers):
    return SimpleNamespace(allowed=True, connected_chargers=tuple(chargers), reason=None)


def blocked(reason="active charging detected"):
    return SimpleNamespace(allowed=False, connected_chargers=("CP1",), reason=reason)


def configure_cutover(tmp_path, monkeypatch, *, preflights=None, calls=None, listener_available=True, connection_marker=1, ocpp_marker=2):
    seed_receipt(tmp_path)
    outcomes = iter(preflights or (allowed("CP1"), allowed("CP1")))
    state_path = tmp_path / "persistent" / "discovered.json"
    monkeypatch.setattr(handoff, "require_root", lambda: None)
    monkeypatch.setattr(handoff, "evaluate_preflight", lambda *args, **kwargs: next(outcomes))
    monkeypatch.setattr(handoff.redirect_tools, "listener_available", lambda port: listener_available)
    monkeypatch.setattr(handoff.redirect_tools, "table_exists", lambda: False)
    monkeypatch.setattr(handoff, "connection_markers", lambda *args: {"CP1": connection_marker})
    monkeypatch.setattr(handoff, "_ocpp_markers", lambda *args: {"CP1": ocpp_marker})
    monkeypatch.setattr(handoff, "discovered_path", lambda state_dir=None: state_path)
    monkeypatch.setattr(handoff.persistence, "persist_ruleset", lambda observed: None)
    monkeypatch.setattr(handoff, "persist_discovered", lambda state_dir, observed: state_path)
    monkeypatch.setattr(handoff, "_stop_service", (lambda service: calls.append(("stop", service))) if calls is not None else (lambda service: None))


def configure_successful_rollback(tmp_path, monkeypatch, calls):
    table_states = iter((False, True))
    monkeypatch.setattr(handoff.redirect_tools, "table_exists", lambda: next(table_states))
    monkeypatch.setattr(handoff, "_start_service", lambda service: calls.append(("start", service)))
    monkeypatch.setattr(handoff, "_service_active", lambda service: True)
    monkeypatch.setattr(handoff.redirect_tools, "apply_redirect", lambda state_dir: calls.append(("apply", str(state_dir))))
    monkeypatch.setattr(handoff.redirect_tools, "remove_redirect", lambda state_dir: calls.append(("remove", str(state_dir))))


def assert_preserved_handoff_only(tmp_path):
    assert (tmp_path / "handoff-endpoint.json").exists()
    assert not (tmp_path / "redirect.json").exists()


def test_prepare_persists_validated_endpoint_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff, "require_root", lambda: None)
    monkeypatch.setattr(handoff, "discover_existing_endpoint", lambda **kwargs: receipt())
    assert handoff.observe_existing_endpoint(state_dir=tmp_path, interface="eth0", listen_port=9000, seconds=15) == receipt()
    assert json.loads((tmp_path / "handoff-endpoint.json").read_text())["destination_port"] == 8888


def test_discovered_round_trip_is_versioned_and_private(tmp_path):
    path = handoff.persist_discovered(tmp_path, receipt())
    payload = json.loads(path.read_text())
    assert path == tmp_path / "discovered.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert payload["kind"] == "redirect"
    assert payload["version"] == 1
    assert handoff.load_discovered(tmp_path) == receipt()


def test_discovered_rejects_extra_fields(tmp_path):
    handoff.persist_discovered(tmp_path, receipt())
    path = tmp_path / "discovered.json"
    payload = json.loads(path.read_text())
    payload["receipt"]["unexpected_authority"] = "0.0.0.0/0"
    path.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="invalid_discovered_receipt"):
        handoff.load_discovered(tmp_path)


def test_discovered_redirect_requires_one_exact_destination(tmp_path):
    ambiguous = RedirectReceipt(interface="eth0", listen_port=9000, source_ip="192.168.129.182", destination_ips=["10.42.0.1", "10.42.0.2"], requests=[WebSocketRequest("10.42.0.1", "10.42.0.1:8888", "/ocpp/CP1"), WebSocketRequest("10.42.0.2", "10.42.0.2:8888", "/ocpp/CP1")], captured_at="discovered", destination_port=8888)
    with pytest.raises(ValueError, match="discovered_redirect_requires_single_destination"):
        handoff.persist_discovered(tmp_path, ambiguous)
    assert not (tmp_path / "discovered.json").exists()


def test_discovered_refuses_to_overwrite_owned_evidence(tmp_path):
    path = handoff.persist_discovered(tmp_path, receipt())
    original = path.read_text()
    with pytest.raises(RuntimeError, match="discovered_receipt_exists"):
        handoff.persist_discovered(tmp_path, receipt())
    assert path.read_text() == original


def test_cutover_promotes_only_after_fresh_connection_and_ocpp(tmp_path, monkeypatch):
    calls = []
    configure_cutover(tmp_path, monkeypatch, calls=calls, connection_marker=7, ocpp_marker=11)
    monkeypatch.setattr(handoff.redirect_tools, "apply_redirect", lambda state_dir: calls.append(("apply", str(state_dir))))
    monkeypatch.setattr(handoff, "wait_for_reconnect", lambda data_dir, markers, **kwargs: calls.append(("reconnect", markers.copy())) or ())
    monkeypatch.setattr(handoff, "wait_for_fresh_ocpp", lambda data_dir, markers, **kwargs: calls.append(("ocpp", markers.copy())) or ())
    monkeypatch.setattr(handoff.persistence, "persist_ruleset", lambda observed: calls.append(("ruleset", observed)))
    monkeypatch.setattr(handoff, "persist_discovered", lambda state_dir, observed: calls.append(("discovered", str(state_dir))) or tmp_path / "persistent" / "discovered.json")
    result = handoff.cutover(data_dir="/data", state_dir=tmp_path, old_service="ocpp-csms.service", persistent_state_dir=tmp_path / "persistent", timeout=15)
    assert result == ("CP1",)
    assert [call[0] for call in calls] == ["stop", "apply", "reconnect", "ocpp", "ruleset", "discovered"]


def test_cutover_refuses_existing_discovered_state_before_service_stop(tmp_path, monkeypatch):
    configure_cutover(tmp_path, monkeypatch)
    state = tmp_path / "persistent" / "discovered.json"
    state.parent.mkdir()
    state.write_text("existing\n")
    monkeypatch.setattr(handoff, "_stop_service", lambda service: pytest.fail("service must remain running"))
    with pytest.raises(RuntimeError, match="discovered_receipt_exists"):
        handoff.cutover(data_dir="/data", state_dir=tmp_path, old_service="old.service")


def test_cutover_final_charging_gate_runs_before_service_stop(tmp_path, monkeypatch):
    configure_cutover(tmp_path, monkeypatch, preflights=(allowed("CP1"), blocked()))
    monkeypatch.setattr(handoff, "_stop_service", lambda service: pytest.fail("service must remain running"))
    with pytest.raises(RuntimeError, match="active charging detected"):
        handoff.cutover(data_dir="/data", state_dir=tmp_path, old_service="old.service")


def test_cutover_rolls_back_on_reconnect_timeout_without_persisting(tmp_path, monkeypatch):
    calls = []
    configure_cutover(tmp_path, monkeypatch, calls=calls)
    configure_successful_rollback(tmp_path, monkeypatch, calls)
    monkeypatch.setattr(handoff, "wait_for_reconnect", lambda *args, **kwargs: ("CP1",))
    monkeypatch.setattr(handoff.persistence, "persist_ruleset", lambda *args: pytest.fail("failed reconnect must not persist"))
    with pytest.raises(RuntimeError, match="charger_reconnect_timeout"):
        handoff.cutover(data_dir="/data", state_dir=tmp_path, old_service="old.service")
    assert calls[-2:] == [("remove", str(tmp_path)), ("start", "old.service")]
    assert_preserved_handoff_only(tmp_path)


def test_cutover_rolls_back_on_fresh_ocpp_timeout_without_persisting(tmp_path, monkeypatch):
    calls = []
    configure_cutover(tmp_path, monkeypatch, calls=calls)
    configure_successful_rollback(tmp_path, monkeypatch, calls)
    monkeypatch.setattr(handoff, "wait_for_reconnect", lambda *args, **kwargs: ())
    monkeypatch.setattr(handoff, "wait_for_fresh_ocpp", lambda *args, **kwargs: ("CP1",))
    monkeypatch.setattr(handoff.persistence, "persist_ruleset", lambda *args: pytest.fail("fresh OCPP timeout must not persist"))
    with pytest.raises(RuntimeError, match="fresh_ocpp_timeout"):
        handoff.cutover(data_dir="/data", state_dir=tmp_path, old_service="old.service")
    assert calls[-2:] == [("remove", str(tmp_path)), ("start", "old.service")]


def test_promote_restores_previous_ruleset_when_discovered_commit_fails(tmp_path, monkeypatch):
    ruleset = tmp_path / "nftables.conf"
    ruleset.write_text("previous\n")
    monkeypatch.setattr(handoff.persistence, "DEFAULT_RULESET_PATH", ruleset)
    monkeypatch.setattr(handoff.persistence, "persist_ruleset", lambda observed: ruleset.write_text("candidate\n"))
    monkeypatch.setattr(handoff, "persist_discovered", lambda *args: (_ for _ in ()).throw(RuntimeError("commit_failed")))
    with pytest.raises(RuntimeError, match="commit_failed"):
        handoff._promote_persistent_adaptation(tmp_path / "persistent", receipt())
    assert ruleset.read_text() == "previous\n"
    assert not (tmp_path / "persistent" / "discovered.json").exists()
