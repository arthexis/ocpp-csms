import pytest

from ocpp_discover import persistence


@pytest.fixture(autouse=True)
def isolate_default_persistent_nftables(request, monkeypatch, tmp_path):
    """Keep orchestration tests from writing the test runner's /etc or invoking nft."""
    if request.module.__name__.endswith("test_persistence"):
        return
    ruleset = tmp_path / "etc" / "ocpp-discover" / "nftables.conf"
    config = tmp_path / "etc" / "nftables.conf"
    monkeypatch.setattr(persistence, "DEFAULT_RULESET_PATH", ruleset)
    monkeypatch.setattr(persistence, "DEFAULT_NFTABLES_CONFIG", config)
    monkeypatch.setattr(persistence, "check_nftables_text", lambda *args, **kwargs: None)

    original_persist = persistence.persist_ruleset
    original_include = persistence.ensure_nftables_include
    original_remove_include = persistence.remove_nftables_include
    original_remove_ruleset = persistence.remove_ruleset

    monkeypatch.setattr(
        persistence,
        "persist_ruleset",
        lambda receipt, *, ruleset_path=None, nft="nft": original_persist(
            receipt,
            ruleset_path=ruleset if ruleset_path is None else ruleset_path,
            nft=nft,
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
    monkeypatch.setattr(
        persistence,
        "remove_ruleset",
        lambda *, ruleset_path=None: original_remove_ruleset(
            ruleset_path=ruleset if ruleset_path is None else ruleset_path
        ),
    )
