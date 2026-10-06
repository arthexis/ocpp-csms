from types import SimpleNamespace

import pytest

from ocpp_discover import first_contact, provisional
from ocpp_discover.redirect import RedirectReceipt, WebSocketRequest


FIELD_SYN = (
    "21:10:21.786183 IP 192.168.129.182.39812 > 10.42.0.1.8888: "
    "Flags [S], seq 1787185088, win 14600, length 0\n"
)


def field_candidate():
    return provisional.LocalSynCandidate(
        interface="eth0",
        source_ip="192.168.129.182",
        destination_ip="10.42.0.1",
        destination_port=8888,
        listen_port=9000,
    )


def field_receipt():
    return RedirectReceipt(
        interface="eth0",
        listen_port=9000,
        source_ip="192.168.129.182",
        destination_ips=["10.42.0.1"],
        requests=[WebSocketRequest("10.42.0.1", "10.42.0.1:8888", "/ocpp/GSCSC082022110005X01")],
        captured_at="discovered",
        destination_port=8888,
    )


def test_field_syn_to_host_local_port_becomes_exact_candidate(monkeypatch):
    monkeypatch.setattr(provisional.discover, "host_addresses", lambda: {"192.168.129.10", "10.42.0.1", "192.168.1.114"})

    assert provisional.local_syn_candidate(FIELD_SYN, interface="eth0", listen_port=9000) == field_candidate()


def test_syn_to_listener_port_does_not_create_provisional_candidate(monkeypatch):
    monkeypatch.setattr(provisional.discover, "host_addresses", lambda: {"10.42.0.1"})
    direct = FIELD_SYN.replace(".8888:", ".9000:")

    assert provisional.local_syn_candidate(direct, interface="eth0", listen_port=9000) is None


def test_provisional_ruleset_is_exactly_scoped(monkeypatch):
    calls = []
    monkeypatch.setattr(provisional.redirect, "require_root", lambda: None)
    monkeypatch.setattr(provisional.redirect, "listener_available", lambda port: port == 9000)
    monkeypatch.setattr(provisional.redirect, "table_exists", lambda: False)
    monkeypatch.setattr(
        provisional.redirect,
        "_run_nft",
        lambda command, input_text=None: calls.append((command, input_text)) or SimpleNamespace(returncode=0, stderr=""),
    )

    ruleset = provisional.apply(field_candidate())

    assert 'iifname "eth0"' in ruleset
    assert "ip saddr 192.168.129.182" in ruleset
    assert "ip daddr 10.42.0.1" in ruleset
    assert "tcp dport 8888 redirect to :9000" in ruleset
    assert calls[0][0] == ["nft", "-c", "-f", "-"]
    assert calls[1][0] == ["nft", "-f", "-"]


def test_local_syn_requires_websocket_and_csms_proof(monkeypatch):
    candidate = field_candidate()
    receipt = field_receipt()
    applied = []
    monkeypatch.setattr(first_contact.provisional, "local_syn_candidate", lambda *args, **kwargs: candidate)
    monkeypatch.setattr(first_contact.provisional, "apply", lambda value: applied.append(value))
    monkeypatch.setattr(first_contact.provisional, "remove", lambda: pytest.fail("proven redirect must remain live"))
    monkeypatch.setattr(first_contact.core, "discover_existing_endpoint", lambda **kwargs: receipt)
    monkeypatch.setattr(first_contact.core, "wait_for_charger", lambda *args, **kwargs: "GSCSC082022110005X01")

    result = first_contact.run_discovery(
        initial_evidence=FIELD_SYN,
        data_dir="/data",
        state_dir="/run/ocpp-discover",
        interface="eth0",
        listen_port=9000,
        tcp_seconds=1,
        connect_timeout=2,
    )

    assert applied == [candidate]
    assert result.charger_id == "GSCSC082022110005X01"
    assert result.receipt == receipt
    assert result.to_json()["status"] == "connected"


def test_unproven_local_syn_redirect_is_removed(monkeypatch):
    candidate = field_candidate()
    removed = []
    monkeypatch.setattr(first_contact.provisional, "local_syn_candidate", lambda *args, **kwargs: candidate)
    monkeypatch.setattr(first_contact.provisional, "apply", lambda value: None)
    monkeypatch.setattr(first_contact.provisional, "remove", lambda: removed.append(True))
    monkeypatch.setattr(first_contact.core, "discover_existing_endpoint", lambda **kwargs: None)

    with pytest.raises(RuntimeError, match="provisional_redirect_unproven"):
        first_contact.run_discovery(
            initial_evidence=FIELD_SYN,
            data_dir="/data",
            state_dir="/run/ocpp-discover",
            interface="eth0",
            listen_port=9000,
            tcp_seconds=1,
            connect_timeout=2,
        )

    assert removed == [True]
