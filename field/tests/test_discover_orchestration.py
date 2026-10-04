import json
from types import SimpleNamespace

import pytest

from field import discover
from field.redirect import RedirectReceipt, WebSocketRequest


def candidate():
    return discover.DiscoveryCandidate("eth0", "aa:bb:cc:dd:ee:ff", "192.168.129.182", "192.168.129.1", 2)


def receipt():
    return RedirectReceipt(
        interface="eth0",
        listen_port=9000,
        source_ip="192.168.129.182",
        destination_ips=["203.0.113.10"],
        requests=[WebSocketRequest("203.0.113.10", "cloud.example", "/ocpp/CP1")],
        captured_at="discovered",
        destination_port=8888,
    )


def prepare_attempt(tmp_path, monkeypatch, *, waits=(None,), redirect_table=False):
    found = candidate()
    wait_results = iter(waits)
    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    monkeypatch.setattr(discover, "wait_for_charger", lambda *args: next(wait_results))
    monkeypatch.setattr(discover.redirect_tools, "table_exists", lambda: redirect_table)
    monkeypatch.setattr(discover, "discover", lambda **kwargs: found)

    def claim(candidate_value, state_dir):
        assert candidate_value == found
        (tmp_path / "address.json").write_text('{"interface":"eth0","address":"192.168.129.1"}')
        return discover.AddressClaim("eth0", "192.168.129.1")

    monkeypatch.setattr(discover, "claim_address", claim)
    return found


def test_connected_chargers_uses_live_status(monkeypatch):
    monkeypatch.setattr(
        discover,
        "appliance_status",
        lambda data_dir: {"chargers": [SimpleNamespace(charger_id="A", connected=False), SimpleNamespace(charger_id="B", connected=True)]},
    )
    assert discover.connected_chargers("/data") == ["B"]


def test_run_exits_during_grace_without_network_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    monkeypatch.setattr(discover, "wait_for_charger", lambda *args: "CP1")
    monkeypatch.setattr(discover, "discover", lambda **kwargs: pytest.fail("ARP discovery must not run"))
    monkeypatch.setattr(discover, "claim_address", lambda *args: pytest.fail("address mutation must not run"))
    monkeypatch.setattr(discover.redirect_tools, "table_exists", lambda: pytest.fail("nft must not run"))
    monkeypatch.setattr(discover.redirect_tools, "apply_redirect", lambda *args: pytest.fail("redirect mutation must not run"))

    result = discover.run_discovery(data_dir="/data", state_dir=tmp_path, grace_seconds=0)

    assert result.status == "already_connected"
    assert result.charger_id == "CP1"
    assert not list(tmp_path.iterdir())


def test_successful_run_preserves_discovered_network_state(tmp_path, monkeypatch):
    found = prepare_attempt(tmp_path, monkeypatch, waits=(None, "CP1"))
    redirect = receipt()
    calls = []
    monkeypatch.setattr(discover, "discover_tcp", lambda *args, **kwargs: redirect)
    monkeypatch.setattr(discover.redirect_tools, "apply_redirect", lambda state_dir: calls.append("apply"))

    result = discover.run_discovery(data_dir="/data", state_dir=tmp_path, grace_seconds=0)

    assert result.status == "connected"
    assert result.charger_id == "CP1"
    assert result.candidate == found
    assert calls == ["apply"]
    assert (tmp_path / "address.json").exists()
    assert json.loads((tmp_path / "redirect.json").read_text())["destination_port"] == 8888
    state = json.loads((tmp_path / "discovery.json").read_text())
    assert state["phase"] == "connected"
    assert state["charger_id"] == "CP1"


def test_tcp_discovery_failure_rolls_back_claim(tmp_path, monkeypatch):
    prepare_attempt(tmp_path, monkeypatch)
    cleaned = []
    monkeypatch.setattr(discover, "discover_tcp", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("tcp_failed")))
    monkeypatch.setattr(discover, "cleanup_address", lambda state_dir: cleaned.append("address") or (tmp_path / "address.json").unlink())

    with pytest.raises(RuntimeError, match="tcp_failed"):
        discover.run_discovery(data_dir="/data", state_dir=tmp_path, grace_seconds=0)

    assert cleaned == ["address"]
    assert not (tmp_path / "discovery.json").exists()


def test_connection_timeout_removes_redirect_and_address(tmp_path, monkeypatch):
    prepare_attempt(tmp_path, monkeypatch, waits=(None, None))
    redirect = receipt()
    cleaned = []
    table_checks = iter([False, True])
    monkeypatch.setattr(discover, "discover_tcp", lambda *args, **kwargs: redirect)
    monkeypatch.setattr(discover.redirect_tools, "apply_redirect", lambda state_dir: None)
    monkeypatch.setattr(discover.redirect_tools, "table_exists", lambda: next(table_checks))
    monkeypatch.setattr(discover.redirect_tools, "remove_redirect", lambda state_dir: cleaned.append("redirect") or (tmp_path / "redirect.json").unlink())
    monkeypatch.setattr(discover, "cleanup_address", lambda state_dir: cleaned.append("address") or (tmp_path / "address.json").unlink())

    with pytest.raises(RuntimeError, match="charger_connection_timeout"):
        discover.run_discovery(data_dir="/data", state_dir=tmp_path, grace_seconds=0, connect_timeout=0)

    assert cleaned == ["redirect", "address"]
    assert not (tmp_path / "discovery.json").exists()


def test_stale_state_is_refused_before_new_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    (tmp_path / "address.json").write_text("{}")
    with pytest.raises(RuntimeError, match="discovery_state_exists"):
        discover.run_discovery(data_dir="/data", state_dir=tmp_path, grace_seconds=0)


def test_existing_redirect_table_is_refused_after_grace(tmp_path, monkeypatch):
    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    monkeypatch.setattr(discover, "wait_for_charger", lambda *args: None)
    monkeypatch.setattr(discover.redirect_tools, "table_exists", lambda: True)
    with pytest.raises(RuntimeError, match="redirect_table_exists"):
        discover.run_discovery(data_dir="/data", state_dir=tmp_path, grace_seconds=0)


def test_cleanup_removes_only_owned_discovery_state(tmp_path, monkeypatch):
    (tmp_path / "address.json").write_text('{"interface":"eth0","address":"192.168.129.1"}')
    (tmp_path / "redirect.json").write_text(json.dumps(receipt().to_json()))
    (tmp_path / "discovery.json").write_text('{"phase":"connected"}')
    calls = []
    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    monkeypatch.setattr(discover.redirect_tools, "table_exists", lambda: True)
    monkeypatch.setattr(discover.redirect_tools, "remove_redirect", lambda state_dir: calls.append("redirect") or (tmp_path / "redirect.json").unlink())
    monkeypatch.setattr(discover, "cleanup_address", lambda state_dir: calls.append("address") or (tmp_path / "address.json").unlink())

    discover.cleanup_discovery(tmp_path)

    assert calls == ["redirect", "address"]
    assert not (tmp_path / "discovery.json").exists()
