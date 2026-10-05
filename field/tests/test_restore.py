import json

import pytest

from field import handoff, restore
from field.redirect import RedirectReceipt, WebSocketRequest


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


def seed_persistent(tmp_path, value=None):
    persistent = tmp_path / "persistent"
    handoff.persist_validated_path_a(persistent, value or receipt())
    return persistent


def configure_runtime(monkeypatch, *, interface=True, addresses=None, listener=True, table=False):
    monkeypatch.setattr(restore.os, "geteuid", lambda: 0)
    monkeypatch.setattr(restore, "_interface_exists", lambda name: interface)
    monkeypatch.setattr(restore, "_host_addresses", lambda: set(addresses or {"172.16.5.1"}))
    monkeypatch.setattr(restore.redirect_tools, "listener_available", lambda port: listener)
    monkeypatch.setattr(restore.redirect_tools, "table_exists", lambda: table)


def test_restore_recreates_exact_narrow_rule(tmp_path, monkeypatch):
    persistent = seed_persistent(tmp_path)
    runtime = tmp_path / "runtime"
    configure_runtime(monkeypatch)
    seen = {}

    def apply(run_dir):
        payload = json.loads((runtime / "redirect.json").read_text())
        restored = restore.redirect_tools.receipt_from_json(payload)
        seen["receipt"] = restored
        return restore.redirect_tools.render_ruleset(restored)

    monkeypatch.setattr(restore.redirect_tools, "apply_redirect", apply)

    rules = restore.restore_path_a(
        persistent_dir=persistent,
        runtime_dir=runtime,
        listen_port=9100,
    )

    assert seen["receipt"] == receipt()
    assert 'iifname "enp2s0" ip saddr 172.16.5.40 ip daddr { 172.16.5.1 } tcp dport 8080 redirect to :9100' in rules
    assert not (runtime / "address.json").exists()


def test_restore_refuses_listener_configuration_mismatch(tmp_path, monkeypatch):
    persistent = seed_persistent(tmp_path)
    configure_runtime(monkeypatch)
    monkeypatch.setattr(restore.redirect_tools, "apply_redirect", lambda run_dir: pytest.fail("must not mutate"))

    with pytest.raises(RuntimeError, match="persistent_path_a_listener_mismatch"):
        restore.restore_path_a(
            persistent_dir=persistent,
            runtime_dir=tmp_path / "runtime",
            listen_port=9200,
        )


def test_restore_refuses_missing_ingress_interface(tmp_path, monkeypatch):
    persistent = seed_persistent(tmp_path)
    configure_runtime(monkeypatch, interface=False)
    monkeypatch.setattr(restore.redirect_tools, "apply_redirect", lambda run_dir: pytest.fail("must not mutate"))

    with pytest.raises(RuntimeError, match="persistent_path_a_interface_missing"):
        restore.restore_path_a(
            persistent_dir=persistent,
            runtime_dir=tmp_path / "runtime",
            listen_port=9100,
        )


def test_restore_refuses_destination_no_longer_owned_by_host(tmp_path, monkeypatch):
    persistent = seed_persistent(tmp_path)
    configure_runtime(monkeypatch, addresses={"192.0.2.10"})
    monkeypatch.setattr(restore.redirect_tools, "apply_redirect", lambda run_dir: pytest.fail("must not mutate"))

    with pytest.raises(RuntimeError, match="persistent_path_a_destination_not_host_owned"):
        restore.restore_path_a(
            persistent_dir=persistent,
            runtime_dir=tmp_path / "runtime",
            listen_port=9100,
        )


def test_restore_refuses_unavailable_target_listener(tmp_path, monkeypatch):
    persistent = seed_persistent(tmp_path)
    configure_runtime(monkeypatch, listener=False)
    monkeypatch.setattr(restore.redirect_tools, "apply_redirect", lambda run_dir: pytest.fail("must not mutate"))

    with pytest.raises(RuntimeError, match="target_listener_unavailable"):
        restore.restore_path_a(
            persistent_dir=persistent,
            runtime_dir=tmp_path / "runtime",
            listen_port=9100,
        )


def test_restore_refuses_existing_owned_table_without_touching_it(tmp_path, monkeypatch):
    persistent = seed_persistent(tmp_path)
    configure_runtime(monkeypatch, table=True)
    monkeypatch.setattr(restore.redirect_tools, "apply_redirect", lambda run_dir: pytest.fail("must not mutate"))
    monkeypatch.setattr(restore.redirect_tools, "remove_redirect", lambda run_dir: pytest.fail("must not remove"))

    with pytest.raises(RuntimeError, match="redirect_table_exists"):
        restore.restore_path_a(
            persistent_dir=persistent,
            runtime_dir=tmp_path / "runtime",
            listen_port=9100,
        )


def test_restore_cleans_runtime_receipt_when_apply_fails(tmp_path, monkeypatch):
    persistent = seed_persistent(tmp_path)
    runtime = tmp_path / "runtime"
    configure_runtime(monkeypatch)
    monkeypatch.setattr(
        restore.redirect_tools,
        "apply_redirect",
        lambda run_dir: (_ for _ in ()).throw(RuntimeError("nft_apply_failed")),
    )

    with pytest.raises(RuntimeError, match="nft_apply_failed"):
        restore.restore_path_a(
            persistent_dir=persistent,
            runtime_dir=runtime,
            listen_port=9100,
        )

    assert not (runtime / "redirect.json").exists()


def test_restore_rejects_malformed_persistent_receipt_before_runtime_mutation(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    persistent.mkdir()
    (persistent / "path-a.json").write_text('{"kind":"ocpp-path-a","version":1,"receipt":{}}')
    configure_runtime(monkeypatch)
    monkeypatch.setattr(restore.redirect_tools, "apply_redirect", lambda run_dir: pytest.fail("must not mutate"))

    with pytest.raises(RuntimeError, match="invalid_persistent_path_a_receipt"):
        restore.restore_path_a(
            persistent_dir=persistent,
            runtime_dir=tmp_path / "runtime",
            listen_port=9100,
        )


def test_restore_requires_root(tmp_path, monkeypatch):
    persistent = seed_persistent(tmp_path)
    monkeypatch.setattr(restore.os, "geteuid", lambda: 1000)

    with pytest.raises(RuntimeError, match="root_required"):
        restore.restore_path_a(
            persistent_dir=persistent,
            runtime_dir=tmp_path / "runtime",
            listen_port=9100,
        )
