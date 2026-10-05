from ocpp_discover import diagnosis, persistence
from ocpp_discover.redirect import RedirectReceipt, WebSocketRequest


def receipt(*, port=8080, path="/ocpp/CP7"):
    return RedirectReceipt(
        interface="enp7s0",
        listen_port=9100,
        source_ip="172.16.5.40",
        destination_ips=["172.16.5.1"],
        requests=[WebSocketRequest("172.16.5.1", f"172.16.5.1:{port}", path)],
        captured_at="test",
        destination_port=port,
    )


def test_inspection_reports_persistent_and_live_configuration(tmp_path, monkeypatch):
    expected = receipt()
    ruleset = tmp_path / "nftables.conf"
    ruleset.write_text(persistence.render_persistent_ruleset(expected))
    monkeypatch.setattr(diagnosis.redirect, "table_exists", lambda: True)

    assert diagnosis.inspect_configuration(expected, ruleset_path=ruleset) == (True, True)


def test_inspection_reports_configuration_mismatch_without_mutation(tmp_path, monkeypatch):
    ruleset = tmp_path / "nftables.conf"
    ruleset.write_text("# different\n")
    monkeypatch.setattr(diagnosis.redirect, "table_exists", lambda: False)

    assert diagnosis.inspect_configuration(receipt(), ruleset_path=ruleset) == (False, False)


def test_passive_observation_ignores_absence(monkeypatch):
    monkeypatch.setattr(diagnosis.discover, "capture_passive_tcp", lambda interface, seconds: "")
    assert diagnosis.observe_contradiction(receipt(), seconds=1) is None


def test_passive_observation_returns_positive_endpoint_contradiction(monkeypatch):
    observed = receipt(port=9999)
    monkeypatch.setattr(diagnosis.discover, "capture_passive_tcp", lambda interface, seconds: "capture")
    monkeypatch.setattr(diagnosis.discover, "_websocket_receipt", lambda *args, **kwargs: observed)

    assert diagnosis.observe_contradiction(receipt(), seconds=1) == observed


def test_same_endpoint_is_not_a_contradiction(monkeypatch):
    expected = receipt()
    monkeypatch.setattr(diagnosis.discover, "capture_passive_tcp", lambda interface, seconds: "capture")
    monkeypatch.setattr(diagnosis.discover, "_websocket_receipt", lambda *args, **kwargs: expected)

    assert diagnosis.observe_contradiction(expected, seconds=1) is None
