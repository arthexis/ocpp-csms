import json
from types import SimpleNamespace

import pytest

from ocpp_discover import discover
from ocpp_discover.redirect import RedirectReceipt, WebSocketRequest
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.runtime import write_pid


def candidate():
    return discover.DiscoveryCandidate("eth0", "aa:bb:cc:dd:ee:ff", "192.168.129.182", "192.168.129.1", 2)


def receipt(destination="203.0.113.10"):
    return RedirectReceipt(
        interface="eth0",
        listen_port=9000,
        source_ip="192.168.129.182",
        destination_ips=[destination],
        requests=[WebSocketRequest(destination, "cloud.example", "/ocpp/CP1")],
        captured_at="discovered",
        destination_port=8888,
    )


def prepare_base(monkeypatch, *, waits=(None,), redirect_tables=(False,)):
    wait_results = iter(waits)
    table_results = iter(redirect_tables)
    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    monkeypatch.setattr(discover, "wait_for_charger", lambda *args: next(wait_results))
    monkeypatch.setattr(discover.redirect_tools, "table_exists", lambda: next(table_results))


def prepare_existing_endpoint(monkeypatch, endpoint, *, waits=(None,), redirect_tables=(False,)):
    prepare_base(monkeypatch, waits=waits, redirect_tables=redirect_tables)
    monkeypatch.setattr(discover, "discover_existing_endpoint", lambda **kwargs: endpoint)
    monkeypatch.setattr(discover, "discover", lambda **kwargs: pytest.fail("ARP fallback must not run"))
    monkeypatch.setattr(discover, "claim_address", lambda *args: pytest.fail("address claim must not run"))


def prepare_arp_fallback(tmp_path, monkeypatch, *, waits=(None,), redirect_tables=(False,)):
    found = candidate()
    prepare_base(monkeypatch, waits=waits, redirect_tables=redirect_tables)
    monkeypatch.setattr(discover, "discover_existing_endpoint", lambda **kwargs: None)
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


def test_connected_chargers_ignore_previous_process_history(tmp_path):
    events = EventStore(tmp_path)
    events.record_runtime("charger_connected", charger_id="CP1")
    events.record_runtime("server_started")
    write_pid(tmp_path)

    assert discover.connected_chargers(tmp_path) == []

    events.record_runtime("charger_connected", charger_id="CP1")

    assert discover.connected_chargers(tmp_path) == ["CP1"]


def test_run_exits_during_grace_without_network_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    monkeypatch.setattr(discover, "wait_for_charger", lambda *args: "CP1")
    monkeypatch.setattr(discover, "discover_existing_endpoint", lambda **kwargs: pytest.fail("TCP discovery must not run"))
    monkeypatch.setattr(discover, "discover", lambda **kwargs: pytest.fail("ARP discovery must not run"))
    monkeypatch.setattr(discover, "claim_address", lambda *args: pytest.fail("address mutation must not run"))
    monkeypatch.setattr(discover.redirect_tools, "table_exists", lambda: pytest.fail("nft must not run"))
    monkeypatch.setattr(discover.redirect_tools, "apply_redirect", lambda *args: pytest.fail("redirect mutation must not run"))

    result = discover.run_discovery(data_dir="/data", state_dir=tmp_path, grace_seconds=0)

    assert result.status == "already_connected"
    assert result.charger_id == "CP1"
    assert not list(tmp_path.iterdir())


def test_existing_endpoint_redirects_without_claiming_address(tmp_path, monkeypatch):
    existing = receipt("10.42.0.1")
    calls = []
    prepare_existing_endpoint(monkeypatch, existing, waits=(None, "CP1"))
    monkeypatch.setattr(discover.redirect_tools, "apply_redirect", lambda state_dir: calls.append("apply"))

    result = discover.run_discovery(data_dir="/data", state_dir=tmp_path, grace_seconds=0)

    assert result.status == "connected"
    assert result.charger_id == "CP1"
    assert result.candidate is None
    assert result.redirect == existing
    assert calls == ["apply"]
    assert not (tmp_path / "address.json").exists()
    assert json.loads((tmp_path / "redirect.json").read_text())["destination_ips"] == ["10.42.0.1"]
    state = json.loads((tmp_path / "discovery.json").read_text())
    assert state["phase"] == "connected"
    assert state["candidate"] is None


def test_existing_endpoint_timeout_removes_redirect_without_address_cleanup(tmp_path, monkeypatch):
    existing = receipt("10.42.0.1")
    cleaned = []
    prepare_existing_endpoint(monkeypatch, existing, waits=(None, None), redirect_tables=(False, True))
    monkeypatch.setattr(discover.redirect_tools, "apply_redirect", lambda state_dir: None)
    monkeypatch.setattr(discover.redirect_tools, "remove_redirect", lambda state_dir: cleaned.append("redirect") or (tmp_path / "redirect.json").unlink())
    monkeypatch.setattr(discover, "cleanup_address", lambda state_dir: pytest.fail("no address was claimed"))

    with pytest.raises(RuntimeError, match="charger_connection_timeout"):
        discover.run_discovery(data_dir="/data", state_dir=tmp_path, grace_seconds=0, connect_timeout=0)

    assert cleaned == ["redirect"]
    assert not (tmp_path / "address.json").exists()
    assert not (tmp_path / "discovery.json").exists()


def test_existing_endpoint_only_stops_before_arp_or_address_claim(tmp_path, monkeypatch):
    prepare_base(monkeypatch, waits=(None,))
    monkeypatch.setattr(discover, "discover_existing_endpoint", lambda **kwargs: None)
    monkeypatch.setattr(discover, "discover", lambda **kwargs: pytest.fail("ARP fallback must not run"))
    monkeypatch.setattr(discover, "claim_address", lambda *args: pytest.fail("address claim must not run"))

    with pytest.raises(RuntimeError, match="no_existing_endpoint_websocket_upgrade"):
        discover.run_discovery(data_dir="/data", state_dir=tmp_path, grace_seconds=0, existing_endpoint_only=True)

    assert not list(tmp_path.iterdir())


def test_passive_diagnostic_returns_endpoint_without_redirect_or_state(tmp_path, monkeypatch):
    existing = receipt("10.42.0.1")
    prepare_base(monkeypatch, waits=(None,))
    monkeypatch.setattr(discover, "discover_existing_endpoint", lambda **kwargs: existing)
    monkeypatch.setattr(discover.redirect_tools, "apply_redirect", lambda *args: pytest.fail("redirect must not run"))

    result = discover.run_discovery(
        data_dir="/data", state_dir=tmp_path, grace_seconds=0, passive_diagnostic_only=True
    )

    assert result.status == "existing_endpoint_observed"
    assert result.redirect == existing
    assert not list(tmp_path.iterdir())


def test_force_passive_capture_bypasses_current_connection_grace(tmp_path, monkeypatch):
    existing = receipt("10.42.0.1")
    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    monkeypatch.setattr(discover, "wait_for_charger", lambda *args: pytest.fail("grace check must not run"))
    monkeypatch.setattr(discover.redirect_tools, "table_exists", lambda: False)
    monkeypatch.setattr(discover, "discover_existing_endpoint", lambda **kwargs: existing)

    result = discover.run_discovery(
        data_dir="/data",
        state_dir=tmp_path,
        force_passive_capture=True,
        passive_diagnostic_only=True,
    )

    assert result.status == "existing_endpoint_observed"
    assert not list(tmp_path.iterdir())


def test_successful_arp_fallback_preserves_discovered_network_state(tmp_path, monkeypatch):
    found = prepare_arp_fallback(tmp_path, monkeypatch, waits=(None, "CP1"))
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


def test_tcp_discovery_failure_rolls_back_claim(tmp_path, monkeypatch):
    prepare_arp_fallback(tmp_path, monkeypatch)
    cleaned = []
    monkeypatch.setattr(discover, "discover_tcp", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("tcp_failed")))
    monkeypatch.setattr(discover, "cleanup_address", lambda state_dir: cleaned.append("address") or (tmp_path / "address.json").unlink())

    with pytest.raises(RuntimeError, match="tcp_failed"):
        discover.run_discovery(data_dir="/data", state_dir=tmp_path, grace_seconds=0)

    assert cleaned == ["address"]
    assert not (tmp_path / "discovery.json").exists()


def test_connection_timeout_removes_redirect_and_address(tmp_path, monkeypatch):
    prepare_arp_fallback(tmp_path, monkeypatch, waits=(None, None), redirect_tables=(False, True))
    redirect = receipt()
    cleaned = []
    monkeypatch.setattr(discover, "discover_tcp", lambda *args, **kwargs: redirect)
    monkeypatch.setattr(discover.redirect_tools, "apply_redirect", lambda state_dir: None)
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
