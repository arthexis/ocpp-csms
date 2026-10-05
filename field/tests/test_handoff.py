import json
from types import SimpleNamespace

import pytest

from field import handoff
from field.redirect import RedirectReceipt, WebSocketRequest
from ocpp_csms.events import EventStore


def receipt():
    return RedirectReceipt(
        interface="eth0",
        listen_port=9000,
        source_ip="192.168.129.182",
        destination_ips=["10.42.0.1"],
        requests=[WebSocketRequest("10.42.0.1", "10.42.0.1:8888", "/ocpp/CP1")],
        captured_at="discovered",
        destination_port=8888,
    )


def seed_receipt(tmp_path):
    (tmp_path / "handoff-endpoint.json").write_text(json.dumps(receipt().to_json()))


def allowed(*chargers):
    return SimpleNamespace(allowed=True, connected_chargers=tuple(chargers), reason=None)


def blocked(reason="active charging detected"):
    return SimpleNamespace(allowed=False, connected_chargers=("CP1",), reason=reason)


def configure_cutover(
    tmp_path,
    monkeypatch,
    *,
    preflights=None,
    calls=None,
    listener_available=True,
    connection_marker=1,
    ocpp_marker=2,
):
    seed_receipt(tmp_path)
    outcomes = iter(preflights or (allowed("CP1"), allowed("CP1")))
    monkeypatch.setattr(handoff, "require_root", lambda: None)
    monkeypatch.setattr(handoff, "evaluate_preflight", lambda *args, **kwargs: next(outcomes))
    monkeypatch.setattr(handoff.redirect_tools, "listener_available", lambda port: listener_available)
    monkeypatch.setattr(handoff.redirect_tools, "table_exists", lambda: False)
    monkeypatch.setattr(handoff, "connection_markers", lambda *args: {"CP1": connection_marker})
    monkeypatch.setattr(handoff, "_ocpp_markers", lambda *args: {"CP1": ocpp_marker})
    if calls is None:
        monkeypatch.setattr(handoff, "_stop_service", lambda service: None)
    else:
        monkeypatch.setattr(handoff, "_stop_service", lambda service: calls.append(("stop", service)))


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
    observed = receipt()
    monkeypatch.setattr(handoff, "require_root", lambda: None)
    monkeypatch.setattr(handoff, "discover_existing_endpoint", lambda **kwargs: observed)

    result = handoff.observe_existing_endpoint(
        state_dir=tmp_path,
        interface="eth0",
        listen_port=9000,
        seconds=15,
    )

    assert result == observed
    path = tmp_path / "handoff-endpoint.json"
    assert path.exists()
    payload = json.loads(path.read_text())
    assert payload["source_ip"] == "192.168.129.182"
    assert payload["destination_ips"] == ["10.42.0.1"]
    assert payload["destination_port"] == 8888
    assert payload["requests"][0]["path"] == "/ocpp/CP1"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["handoff-endpoint.json"]


def test_prepare_refuses_to_overwrite_existing_evidence(tmp_path, monkeypatch):
    path = tmp_path / "handoff-endpoint.json"
    path.write_text("original\n")
    monkeypatch.setattr(handoff, "require_root", lambda: None)
    monkeypatch.setattr(
        handoff,
        "discover_existing_endpoint",
        lambda **kwargs: pytest.fail("capture must not run when evidence already exists"),
    )

    with pytest.raises(RuntimeError, match="handoff_receipt_exists"):
        handoff.observe_existing_endpoint(
            state_dir=tmp_path,
            interface="eth0",
            listen_port=9000,
            seconds=15,
        )

    assert path.read_text() == "original\n"


def test_prepare_does_not_persist_unvalidated_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff, "require_root", lambda: None)
    monkeypatch.setattr(handoff, "discover_existing_endpoint", lambda **kwargs: None)

    with pytest.raises(RuntimeError, match="no_existing_endpoint_websocket_upgrade"):
        handoff.observe_existing_endpoint(
            state_dir=tmp_path,
            interface="eth0",
            listen_port=9000,
            seconds=15,
        )

    assert not list(tmp_path.iterdir())


def test_load_receipt_round_trips_persisted_endpoint(tmp_path):
    expected = receipt()
    path = tmp_path / "handoff-endpoint.json"
    path.write_text(json.dumps(expected.to_json()))

    loaded = handoff.load_receipt(tmp_path)

    assert loaded == expected


def test_load_receipt_rejects_invalid_evidence(tmp_path):
    (tmp_path / "handoff-endpoint.json").write_text('{"interface":"eth0"}')

    with pytest.raises(RuntimeError, match="invalid_handoff_receipt"):
        handoff.load_receipt(tmp_path)


def test_cutover_uses_prevalidated_receipt_and_requires_fresh_connection_and_ocpp(tmp_path, monkeypatch):
    calls = []
    configure_cutover(
        tmp_path,
        monkeypatch,
        calls=calls,
        connection_marker=7,
        ocpp_marker=11,
    )

    def apply(state_dir):
        assert json.loads((tmp_path / "redirect.json").read_text())["destination_port"] == 8888
        calls.append(("apply", str(state_dir)))

    monkeypatch.setattr(handoff.redirect_tools, "apply_redirect", apply)
    monkeypatch.setattr(
        handoff,
        "wait_for_reconnect",
        lambda data_dir, markers, **kwargs: calls.append(("reconnect", markers.copy())) or (),
    )
    monkeypatch.setattr(
        handoff,
        "wait_for_fresh_ocpp",
        lambda data_dir, markers, **kwargs: calls.append(("ocpp", markers.copy())) or (),
    )

    result = handoff.cutover(
        data_dir="/data",
        state_dir=tmp_path,
        old_service="ocpp-csms.service",
        timeout=15,
    )

    assert result == ("CP1",)
    assert calls == [
        ("stop", "ocpp-csms.service"),
        ("apply", str(tmp_path)),
        ("reconnect", {"CP1": 7}),
        ("ocpp", {"CP1": 11}),
    ]
    assert (tmp_path / "handoff-endpoint.json").exists()
    assert (tmp_path / "redirect.json").exists()


def test_cutover_final_charging_gate_runs_before_service_stop(tmp_path, monkeypatch):
    configure_cutover(
        tmp_path,
        monkeypatch,
        preflights=(allowed("CP1"), blocked()),
    )
    monkeypatch.setattr(handoff, "_stop_service", lambda service: pytest.fail("service must remain running"))

    with pytest.raises(RuntimeError, match="active charging detected"):
        handoff.cutover(data_dir="/data", state_dir=tmp_path, old_service="old.service")

    assert not (tmp_path / "redirect.json").exists()


def test_cutover_refuses_if_connected_set_changes_before_disruption(tmp_path, monkeypatch):
    configure_cutover(
        tmp_path,
        monkeypatch,
        preflights=(allowed("CP1"), allowed("CP2")),
    )
    monkeypatch.setattr(handoff, "_stop_service", lambda service: pytest.fail("service must remain running"))

    with pytest.raises(RuntimeError, match="connected_chargers_changed_before_cutover"):
        handoff.cutover(data_dir="/data", state_dir=tmp_path, old_service="old.service")


def test_cutover_refuses_before_disruption_when_target_listener_is_missing(tmp_path, monkeypatch):
    configure_cutover(tmp_path, monkeypatch, listener_available=False)
    monkeypatch.setattr(handoff, "_stop_service", lambda service: pytest.fail("service must remain running"))

    with pytest.raises(RuntimeError, match="target_listener_unavailable"):
        handoff.cutover(data_dir="/data", state_dir=tmp_path, old_service="old.service")


def test_cutover_rolls_back_if_redirect_apply_fails(tmp_path, monkeypatch):
    calls = []
    configure_cutover(tmp_path, monkeypatch, calls=calls)
    monkeypatch.setattr(handoff, "_start_service", lambda service: calls.append(("start", service)))
    monkeypatch.setattr(handoff, "_service_active", lambda service: True)
    monkeypatch.setattr(
        handoff.redirect_tools,
        "apply_redirect",
        lambda state_dir: (_ for _ in ()).throw(RuntimeError("nft_apply_failed")),
    )

    with pytest.raises(RuntimeError, match="nft_apply_failed"):
        handoff.cutover(data_dir="/data", state_dir=tmp_path, old_service="old.service")

    assert calls == [("stop", "old.service"), ("start", "old.service")]
    assert_preserved_handoff_only(tmp_path)


def test_cutover_rolls_back_redirect_and_old_service_on_reconnect_timeout(tmp_path, monkeypatch):
    calls = []
    configure_cutover(tmp_path, monkeypatch, calls=calls)
    configure_successful_rollback(tmp_path, monkeypatch, calls)
    monkeypatch.setattr(handoff, "wait_for_reconnect", lambda *args, **kwargs: ("CP1",))

    with pytest.raises(RuntimeError, match="charger_reconnect_timeout"):
        handoff.cutover(data_dir="/data", state_dir=tmp_path, old_service="old.service")

    assert calls == [
        ("stop", "old.service"),
        ("apply", str(tmp_path)),
        ("remove", str(tmp_path)),
        ("start", "old.service"),
    ]
    assert_preserved_handoff_only(tmp_path)


def test_cutover_rolls_back_on_fresh_ocpp_timeout(tmp_path, monkeypatch):
    calls = []
    configure_cutover(tmp_path, monkeypatch, calls=calls)
    configure_successful_rollback(tmp_path, monkeypatch, calls)
    monkeypatch.setattr(handoff, "wait_for_reconnect", lambda *args, **kwargs: ())
    monkeypatch.setattr(handoff, "wait_for_fresh_ocpp", lambda *args, **kwargs: ("CP1",))

    with pytest.raises(RuntimeError, match="fresh_ocpp_timeout"):
        handoff.cutover(data_dir="/data", state_dir=tmp_path, old_service="old.service")

    assert calls[-2:] == [("remove", str(tmp_path)), ("start", "old.service")]
    assert_preserved_handoff_only(tmp_path)


def test_cutover_reports_rollback_failure_without_losing_original_error(tmp_path, monkeypatch):
    configure_cutover(tmp_path, monkeypatch)
    monkeypatch.setattr(handoff.redirect_tools, "apply_redirect", lambda state_dir: None)
    monkeypatch.setattr(handoff, "wait_for_reconnect", lambda *args, **kwargs: ("CP1",))
    monkeypatch.setattr(handoff, "_start_service", lambda service: (_ for _ in ()).throw(RuntimeError("start_failed")))

    with pytest.raises(RuntimeError, match="charger_reconnect_timeout: CP1; rollback failed: old service restart failed: start_failed"):
        handoff.cutover(data_dir="/data", state_dir=tmp_path, old_service="old.service")


def test_fresh_ocpp_requires_new_inbound_event_per_expected_charger(tmp_path):
    events = EventStore(tmp_path)
    events.record_ocpp("CP1", "Heartbeat", {})
    baseline = handoff._ocpp_markers(tmp_path, ("CP1", "CP2"))

    assert handoff.wait_for_fresh_ocpp(tmp_path, baseline, timeout=0) == ("CP1", "CP2")

    events.record_ocpp("CP1", "Heartbeat", {})
    assert handoff.wait_for_fresh_ocpp(tmp_path, baseline, timeout=0) == ("CP2",)

    events.record_ocpp("CP2", "BootNotification", {})
    assert handoff.wait_for_fresh_ocpp(tmp_path, baseline, timeout=0) == ()
