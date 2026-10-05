import pytest

from ocpp_discover import persistence


@pytest.fixture(autouse=True)
def isolate_default_persistent_nftables(request, monkeypatch, tmp_path):
    """Keep orchestration tests from writing the test runner's /etc."""
    if request.module.__name__.endswith("test_persistence"):
        return
    ruleset = tmp_path / "etc" / "ocpp-discover" / "redirect.nft"
    config = tmp_path / "etc" / "nftables.conf"
    monkeypatch.setattr(persistence, "DEFAULT_RULESET_PATH", ruleset)
    monkeypatch.setattr(persistence, "DEFAULT_NFTABLES_CONFIG", config)

    original_persist = persistence.persist_ruleset
    original_include = persistence.ensure_nftables_include
    original_remove_include = persistence.remove_nftables_include

    monkeypatch.setattr(
        persistence,
        "persist_ruleset",
        lambda receipt, *, ruleset_path=None: original_persist(
            receipt, ruleset_path=ruleset if ruleset_path is None else ruleset_path
        ),
    )
    monkeypatch.setattr(
        persistence,
        "ensure_nftables_include",
        lambda *, config_path=None, include_line=persistence.INCLUDE_LINE: original_include(
            config_path=config if config_path is None else config_path,
            include_line=include_line,
        ),
    )
    monkeypatch.setattr(
        persistence,
        "remove_nftables_include",
        lambda *, config_path=None, include_line=persistence.INCLUDE_LINE: original_remove_include(
            config_path=config if config_path is None else config_path,
            include_line=include_line,
        ),
    )
