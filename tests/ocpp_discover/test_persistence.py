from pathlib import Path

import pytest

from ocpp_discover import persistence
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


def test_persist_ruleset_writes_exact_narrow_redirect(tmp_path):
    path = tmp_path / "etc" / "ocpp-discover" / "nftables.conf"
    result = persistence.persist_ruleset(receipt(), ruleset_path=path)

    assert result == path
    text = path.read_text()
    assert "table ip ocpp_field_redirect" in text
    assert 'iifname "enp2s0"' in text
    assert "ip saddr 172.16.5.40" in text
    assert "ip daddr { 172.16.5.1 }" in text
    assert "tcp dport 8080 redirect to :9100" in text
    assert path.stat().st_mode & 0o777 == 0o600


def test_persist_ruleset_can_promote_initial_empty_install_fragment(tmp_path):
    path = tmp_path / "nftables.conf"
    persistence.ensure_ruleset_file(ruleset_path=path)
    persistence.persist_ruleset(receipt(), ruleset_path=path)
    assert "table ip ocpp_field_redirect" in path.read_text()


def test_persist_ruleset_is_idempotent_only_for_exact_match(tmp_path):
    path = tmp_path / "nftables.conf"
    persistence.persist_ruleset(receipt(), ruleset_path=path)
    persistence.persist_ruleset(receipt(), ruleset_path=path)
    path.write_text("different\n")

    with pytest.raises(RuntimeError, match="conflicting_persistent_nftables_adaptation"):
        persistence.persist_ruleset(receipt(), ruleset_path=path)


def test_ensure_nftables_include_preserves_existing_config(tmp_path):
    config = tmp_path / "nftables.conf"
    config.write_text("#!/usr/sbin/nft -f\n\ntable inet existing {}\n")

    assert persistence.ensure_nftables_include(config_path=config) is True
    text = config.read_text()
    assert "table inet existing {}" in text
    assert persistence.INCLUDE_LINE in text
    assert persistence.ensure_nftables_include(config_path=config) is False
    assert config.read_text() == text


def test_prepare_integration_creates_inert_fragment_and_checks_both_files(tmp_path, monkeypatch):
    ruleset = tmp_path / "ocpp-discover" / "nftables.conf"
    config = tmp_path / "nftables.conf"
    config.write_text("#!/usr/sbin/nft -f\n")
    checked = []
    monkeypatch.setattr(persistence, "check_nftables_file", lambda path, **kwargs: checked.append(Path(path)))

    created, changed = persistence.prepare_nftables_integration(ruleset_path=ruleset, config_path=config)

    assert created is True
    assert changed is True
    assert ruleset.read_text() == persistence.EMPTY_RULESET
    assert persistence.INCLUDE_LINE in config.read_text()
    assert checked == [ruleset, config]


def test_prepare_integration_rolls_back_new_files_if_combined_check_fails(tmp_path, monkeypatch):
    ruleset = tmp_path / "ocpp-discover" / "nftables.conf"
    config = tmp_path / "nftables.conf"
    original = "#!/usr/sbin/nft -f\n\ntable inet existing {}\n"
    config.write_text(original)
    calls = []

    def fail_second(path, **kwargs):
        calls.append(Path(path))
        if len(calls) == 2:
            raise RuntimeError("invalid_nftables_configuration")

    monkeypatch.setattr(persistence, "check_nftables_file", fail_second)
    with pytest.raises(RuntimeError, match="invalid_nftables_configuration"):
        persistence.prepare_nftables_integration(ruleset_path=ruleset, config_path=config)

    assert config.read_text() == original
    assert not ruleset.exists()


def test_remove_include_removes_only_discover_owned_lines(tmp_path):
    config = tmp_path / "nftables.conf"
    config.write_text(
        "#!/usr/sbin/nft -f\n\ntable inet existing {}\n\n"
        f"{persistence.OWNED_INCLUDE_COMMENT}\n"
        f"{persistence.INCLUDE_LINE}\n"
    )

    assert persistence.remove_nftables_include(config_path=config) is True
    assert config.read_text() == "#!/usr/sbin/nft -f\n\ntable inet existing {}\n"


def test_default_paths_use_discover_owned_fragment_and_debian_loader():
    assert persistence.DEFAULT_DISCOVERED_PATH == Path("/var/lib/ocpp-discover/discovered.json")
    assert persistence.DEFAULT_RULESET_PATH == Path("/etc/ocpp-discover/nftables.conf")
    assert persistence.DEFAULT_NFTABLES_CONFIG == Path("/etc/nftables.conf")
