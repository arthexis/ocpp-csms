from types import SimpleNamespace

import pytest

from ocpp_discover import handoff, persistence, reconcile
from ocpp_discover.redirect import RedirectReceipt, WebSocketRequest


def receipt(port):
    return RedirectReceipt(
        interface="enp7s0",
        listen_port=9100,
        source_ip="172.16.5.40",
        destination_ips=["172.16.5.1"],
        requests=[WebSocketRequest("172.16.5.1", f"172.16.5.1:{port}", "/ocpp/CP7")],
        captured_at="test",
        destination_port=port,
    )


def allow_preflight(monkeypatch):
    monkeypatch.setattr(reconcile, "evaluate_preflight", lambda data_dir, rollover: SimpleNamespace(allowed=True, reason=None))


def test_reconcile_requires_no_active_charging(tmp_path, monkeypatch):
    monkeypatch.setattr(reconcile, "evaluate_preflight", lambda data_dir, rollover: SimpleNamespace(allowed=False, reason="active charging detected"))
    monkeypatch.setattr(reconcile, "_replace_live", lambda candidate: pytest.fail("must not mutate while charging"))

    with pytest.raises(RuntimeError, match="active charging"):
        reconcile.reconcile(data_dir=tmp_path / "data", persistent_dir=tmp_path / "state", expected=receipt(8080), candidate=receipt(9999))


def test_reconcile_proves_candidate_before_durable_commit(tmp_path, monkeypatch):
    old = receipt(8080)
    candidate = receipt(9999)
    state = tmp_path / "state"
    ruleset = tmp_path / "nftables.conf"
    handoff.persist_discovered(state, old)
    ruleset.write_text(persistence.render_persistent_ruleset(old))
    allow_preflight(monkeypatch)
    calls = []
    monkeypatch.setattr(reconcile, "connection_markers", lambda data_dir, chargers: {"CP7": 4})
    monkeypatch.setattr(handoff, "_ocpp_markers", lambda data_dir, chargers: {"CP7": 8})
    monkeypatch.setattr(reconcile, "_replace_live", lambda value: calls.append(("live", value.destination_port)))
    monkeypatch.setattr(reconcile, "wait_for_reconnect", lambda data_dir, markers, timeout: calls.append(("connection", markers)) or ())
    monkeypatch.setattr(handoff, "wait_for_fresh_ocpp", lambda data_dir, markers, timeout: calls.append(("ocpp", markers)) or ())
    monkeypatch.setattr(persistence, "check_nftables_text", lambda text: calls.append(("check", candidate.destination_port)))

    result = reconcile.reconcile(data_dir=tmp_path / "data", persistent_dir=state, expected=old, candidate=candidate, ruleset_path=ruleset)

    assert result == candidate
    assert handoff.load_discovered(state) == candidate
    assert ruleset.read_text() == persistence.render_persistent_ruleset(candidate)
    assert calls[:3] == [("live", 9999), ("connection", {"CP7": 4}), ("ocpp", {"CP7": 8})]


def test_reconnect_failure_restores_old_live_adaptation_and_keeps_durable_state(tmp_path, monkeypatch):
    old = receipt(8080)
    candidate = receipt(9999)
    state = tmp_path / "state"
    ruleset = tmp_path / "nftables.conf"
    handoff.persist_discovered(state, old)
    ruleset.write_text(persistence.render_persistent_ruleset(old))
    allow_preflight(monkeypatch)
    live = []
    monkeypatch.setattr(reconcile, "connection_markers", lambda data_dir, chargers: {"CP7": 0})
    monkeypatch.setattr(handoff, "_ocpp_markers", lambda data_dir, chargers: {"CP7": 0})
    monkeypatch.setattr(reconcile, "_replace_live", lambda value: live.append(value.destination_port))
    monkeypatch.setattr(reconcile, "wait_for_reconnect", lambda data_dir, markers, timeout: ("CP7",))

    with pytest.raises(RuntimeError, match="reconciliation_reconnect_timeout"):
        reconcile.reconcile(data_dir=tmp_path / "data", persistent_dir=state, expected=old, candidate=candidate, ruleset_path=ruleset)

    assert live == [9999, 8080]
    assert handoff.load_discovered(state) == old
    assert ruleset.read_text() == persistence.render_persistent_ruleset(old)


def test_ocpp_failure_also_rolls_back_live_candidate(tmp_path, monkeypatch):
    old = receipt(8080)
    candidate = receipt(9999)
    state = tmp_path / "state"
    ruleset = tmp_path / "nftables.conf"
    handoff.persist_discovered(state, old)
    ruleset.write_text(persistence.render_persistent_ruleset(old))
    allow_preflight(monkeypatch)
    live = []
    monkeypatch.setattr(reconcile, "connection_markers", lambda data_dir, chargers: {"CP7": 0})
    monkeypatch.setattr(handoff, "_ocpp_markers", lambda data_dir, chargers: {"CP7": 0})
    monkeypatch.setattr(reconcile, "_replace_live", lambda value: live.append(value.destination_port))
    monkeypatch.setattr(reconcile, "wait_for_reconnect", lambda data_dir, markers, timeout: ())
    monkeypatch.setattr(handoff, "wait_for_fresh_ocpp", lambda data_dir, markers, timeout: ("CP7",))

    with pytest.raises(RuntimeError, match="reconciliation_ocpp_timeout"):
        reconcile.reconcile(data_dir=tmp_path / "data", persistent_dir=state, expected=old, candidate=candidate, ruleset_path=ruleset)

    assert live == [9999, 8080]
    assert handoff.load_discovered(state) == old
