from ocpp_discover import handoff, lifecycle
from ocpp_discover.redirect import RedirectReceipt, WebSocketRequest


def receipt():
    return RedirectReceipt(
        interface="enp2s0",
        listen_port=9100,
        source_ip="172.16.5.40",
        destination_ips=["172.16.5.1"],
        requests=[WebSocketRequest("172.16.5.1", "172.16.5.1:8080", "/ocpp/CP7")],
        captured_at="2026-10-05T00:00:00+00:00",
        destination_port=8080,
    )


def test_prepare_reports_absent_without_creating_state(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    prepared = []
    monkeypatch.setattr(lifecycle.persistence, "prepare_nftables_integration", lambda: prepared.append(True))
    assert lifecycle.prepare_persistent_state(persistent_dir=persistent) == "absent"
    assert prepared == [True]
    assert not persistent.exists()


def test_prepare_preserves_valid_discovered_receipt(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    handoff.persist_validated_path_a(persistent, receipt())
    prepared = []
    monkeypatch.setattr(lifecycle.persistence, "prepare_nftables_integration", lambda: prepared.append(True))

    assert lifecycle.prepare_persistent_state(persistent_dir=persistent) == "preserved"
    assert prepared == [True]
    assert handoff.load_persistent_path_a(persistent) == receipt()


def test_remove_deletes_only_discovered_receipt_from_state_dir(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    handoff.persist_validated_path_a(persistent, receipt())
    unrelated = persistent / "operator-note.txt"
    unrelated.write_text("keep\n")
    monkeypatch.setattr(lifecycle.persistence, "remove_ruleset", lambda **kwargs: False)
    monkeypatch.setattr(lifecycle.persistence, "remove_nftables_include", lambda **kwargs: False)

    removed = lifecycle.remove_persistent_state(persistent_dir=persistent)

    assert removed == (handoff.persistent_receipt_path(persistent),)
    assert unrelated.read_text() == "keep\n"
    assert persistent.exists()


def test_remove_is_idempotent_when_discovered_receipt_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle.persistence, "remove_ruleset", lambda **kwargs: False)
    monkeypatch.setattr(lifecycle.persistence, "remove_nftables_include", lambda **kwargs: False)
    assert lifecycle.remove_persistent_state(persistent_dir=tmp_path / "persistent") == ()


def test_default_persistent_namespace_belongs_to_discover():
    assert str(lifecycle.DEFAULT_PERSISTENT_DIR) == "/var/lib/ocpp-discover"
    assert handoff.persistent_receipt_path() == lifecycle.DEFAULT_PERSISTENT_DIR / "discovered.json"
