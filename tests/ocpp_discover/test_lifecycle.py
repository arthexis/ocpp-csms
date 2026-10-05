import json

import pytest

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


def test_migration_moves_validated_legacy_receipt(tmp_path):
    legacy = tmp_path / "legacy"
    persistent = tmp_path / "persistent"
    handoff.persist_validated_path_a(legacy, receipt())

    result = lifecycle.migrate_persistent_state(legacy_dir=legacy, persistent_dir=persistent)

    assert result == "migrated"
    assert handoff.load_persistent_path_a(persistent) == receipt()
    assert not handoff.persistent_receipt_path(legacy).exists()


def test_migration_preserves_valid_new_receipt(tmp_path):
    legacy = tmp_path / "legacy"
    persistent = tmp_path / "persistent"
    handoff.persist_validated_path_a(persistent, receipt())

    assert lifecycle.migrate_persistent_state(legacy_dir=legacy, persistent_dir=persistent) == "preserved"
    assert handoff.load_persistent_path_a(persistent) == receipt()


def test_migration_refuses_competing_old_and_new_authority(tmp_path):
    legacy = tmp_path / "legacy"
    persistent = tmp_path / "persistent"
    handoff.persist_validated_path_a(legacy, receipt())
    handoff.persist_validated_path_a(persistent, receipt())

    with pytest.raises(RuntimeError, match="conflicting_persistent_discover_state"):
        lifecycle.migrate_persistent_state(legacy_dir=legacy, persistent_dir=persistent)


def test_migration_rejects_invalid_legacy_receipt_without_creating_new_state(tmp_path):
    legacy = tmp_path / "legacy"
    persistent = tmp_path / "persistent"
    legacy.mkdir()
    handoff.persistent_receipt_path(legacy).write_text("not-json\n")

    with pytest.raises(RuntimeError, match="invalid_persistent_path_a_receipt"):
        lifecycle.migrate_persistent_state(legacy_dir=legacy, persistent_dir=persistent)

    assert not handoff.persistent_receipt_path(persistent).exists()


def test_remove_deletes_only_known_durable_receipts(tmp_path):
    legacy = tmp_path / "legacy"
    persistent = tmp_path / "persistent"
    handoff.persist_validated_path_a(legacy, receipt())
    handoff.persist_validated_path_a(persistent, receipt())
    unrelated = persistent / "operator-note.txt"
    unrelated.write_text("keep\n")

    removed = lifecycle.remove_persistent_state(legacy_dir=legacy, persistent_dir=persistent)

    assert set(removed) == {
        handoff.persistent_receipt_path(legacy),
        handoff.persistent_receipt_path(persistent),
    }
    assert unrelated.read_text() == "keep\n"
    assert persistent.exists()


def test_remove_is_idempotent_when_receipts_are_absent(tmp_path):
    assert lifecycle.remove_persistent_state(
        legacy_dir=tmp_path / "legacy",
        persistent_dir=tmp_path / "persistent",
    ) == ()


def test_default_persistent_namespace_belongs_to_discover():
    assert str(lifecycle.DEFAULT_PERSISTENT_DIR) == "/var/lib/ocpp-discover"
    assert handoff.persistent_receipt_path() == lifecycle.DEFAULT_PERSISTENT_DIR / "path-a.json"
