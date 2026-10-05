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
    path = tmp_path / "etc" / "ocpp-discover" / "redirect.nft"
    result = persistence.persist_ruleset(receipt(), ruleset_path=path)

    assert result == path
    text = path.read_text()
    assert "table ip ocpp_field_redirect" in text
    assert 'iifname "enp2s0"' in text
    assert "ip saddr 172.16.5.40" in text
    assert "ip daddr { 172.16.5.1 }" in text
    assert "tcp dport 8080 redirect to :9100" in text
    assert path.stat().st_mode & 0o777 == 0o600


def test_persist_ruleset_is_idempotent_only_for_exact_match(tmp_path):
    path = tmp_path / "redirect.nft"
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


def test_remove_include_removes_only_discover_owned_lines(tmp_path):
    config = tmp_path / "nftables.conf"
    config.write_text(
        "#!/usr/sbin/nft -f\n\ntable inet existing {}\n\n"
        "# OCPP Discover validated adaptation\n"
        f"{persistence.INCLUDE_LINE}\n"
    )

    assert persistence.remove_nftables_include(config_path=config) is True
    assert config.read_text() == "#!/usr/sbin/nft -f\n\ntable inet existing {}\n"


def test_default_paths_use_discover_owned_fragment_and_debian_loader():
    assert persistence.DEFAULT_DISCOVERED_PATH == Path("/var/lib/ocpp-discover/discovered.json")
    assert persistence.DEFAULT_RULESET_PATH == Path("/etc/ocpp-discover/redirect.nft")
    assert persistence.DEFAULT_NFTABLES_CONFIG == Path("/etc/nftables.conf")
