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
    monkeypatch.setenv("OCPP_DISCOVER_ALLOW_LOCAL_8888", "1")
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
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", lambda *args: "capture")
    monkeypatch.setattr(first_contact.core, "_websocket_receipt", lambda *args, **kwargs: receipt)
    monkeypatch.setattr(first_contact.core, "connected_chargers", lambda *args, **kwargs: ["GSCSC082022110005X01"])
    monkeypatch.setattr(first_contact.handoff, "wait_for_fresh_ocpp", lambda *args, **kwargs: ())

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
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", lambda *args: "capture")
    monkeypatch.setattr(first_contact.core, "_websocket_receipt", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("no_plaintext_websocket_upgrade")))

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

@pytest.mark.parametrize("port", [22, 80, 443, 9000, 5432])
def test_unrelated_ports_never_become_provisional_candidates(monkeypatch, port):
    monkeypatch.setattr(provisional.discover, "host_addresses", lambda: {"192.168.129.1"})
    line = f"12:00:00.000000 IP 192.168.129.184.50000 > 192.168.129.1.{port}: Flags [S], seq 1\n"
    assert provisional.local_syn_candidate(line, interface="eth0", listen_port=9000) is None


def test_external_plaintext_syn_becomes_exact_scoped_candidate(monkeypatch):
    monkeypatch.setattr(provisional.discover, "host_addresses", lambda: {"192.168.129.1"})
    line = "12:00:00.000000 IP 192.168.129.184.50000 > 198.18.0.1.80: Flags [S], seq 1\n"
    candidate = provisional.local_syn_candidate(line, interface="eth0", listen_port=9000)
    assert candidate.destination_ip == "198.18.0.1"
    assert "ip daddr 198.18.0.1 tcp dport 80 redirect to :9000" in provisional._ruleset(candidate)


def test_proof_is_scoped_to_exact_source_and_destination(monkeypatch):
    candidate = field_candidate()
    monkeypatch.setattr(first_contact.provisional, "local_syn_candidate", lambda *a, **k: candidate)
    monkeypatch.setattr(first_contact.provisional, "apply", lambda c: None)
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", lambda *args: "capture")
    def parser(capture, **kwargs):
        assert kwargs["source_ip"] == candidate.source_ip
        assert kwargs["destination_filter"] == {candidate.destination_ip}
        return field_receipt()
    monkeypatch.setattr(first_contact.core, "_websocket_receipt", parser)
    monkeypatch.setattr(first_contact.core, "connected_chargers", lambda *a, **k: ["GSCSC082022110005X01"])
    monkeypatch.setattr(first_contact.handoff, "wait_for_fresh_ocpp", lambda *a, **k: ())
    result = first_contact.run_discovery(initial_evidence=FIELD_SYN, data_dir="data")
    assert result.charger_id == "GSCSC082022110005X01"


def test_other_connected_charger_does_not_prove_endpoint(monkeypatch):
    removed = []
    monkeypatch.setattr(first_contact.provisional, "local_syn_candidate", lambda *a, **k: field_candidate())
    monkeypatch.setattr(first_contact.provisional, "apply", lambda c: None)
    monkeypatch.setattr(first_contact.provisional, "remove", lambda: removed.append(True))
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", lambda *a: "capture")
    monkeypatch.setattr(first_contact.core, "_websocket_receipt", lambda *a, **k: field_receipt())
    monkeypatch.setattr(first_contact.core, "connected_chargers", lambda *a, **k: ["OTHER-CHARGER"])
    with pytest.raises(RuntimeError, match="charger_connection_timeout"):
        first_contact.run_discovery(initial_evidence=FIELD_SYN, data_dir="data", connect_timeout=0)
    assert removed == [True]


def test_local_8888_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("OCPP_DISCOVER_ALLOW_LOCAL_8888", raising=False)
    monkeypatch.setattr(provisional.discover, "host_addresses", lambda: {"10.42.0.1"})
    assert provisional.local_syn_candidate(FIELD_SYN, interface="eth0", listen_port=9000) is None


def test_termination_removes_temporary_redirect(monkeypatch):
    removed = []
    monkeypatch.setattr(first_contact.provisional, "local_syn_candidate", lambda *a, **k: field_candidate())
    monkeypatch.setattr(first_contact.provisional, "apply", lambda c: None)
    monkeypatch.setattr(first_contact.provisional, "remove", lambda: removed.append(True))
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", lambda *a: (_ for _ in ()).throw(SystemExit(143)))
    with pytest.raises(SystemExit):
        first_contact.run_discovery(initial_evidence=FIELD_SYN, data_dir="data")
    assert removed == [True]


def test_connection_without_fresh_ocpp_is_not_proven(monkeypatch):
    removed = []
    monkeypatch.setattr(first_contact.provisional, "local_syn_candidate", lambda *a, **k: field_candidate())
    monkeypatch.setattr(first_contact.provisional, "apply", lambda c: None)
    monkeypatch.setattr(first_contact.core, "connected_chargers", lambda *a: ["GSCSC082022110005X01"])
    monkeypatch.setattr(first_contact.provisional, "remove", lambda: removed.append(True))
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", lambda *a: "capture")
    monkeypatch.setattr(first_contact.core, "_websocket_receipt", lambda *a, **k: field_receipt())
    monkeypatch.setattr(first_contact.core, "wait_for_charger", lambda *a, **k: "GSCSC082022110005X01")
    monkeypatch.setattr(first_contact.handoff, "wait_for_fresh_ocpp", lambda *a, **k: ("GSCSC082022110005X01",))
    with pytest.raises(RuntimeError, match="fresh_ocpp_timeout"):
        first_contact.run_discovery(initial_evidence=FIELD_SYN, data_dir="data")
    assert removed == [True]


def test_failed_nft_apply_removes_partial_table(monkeypatch):
    calls = []
    existing = [False]
    monkeypatch.setattr(provisional.redirect, "require_root", lambda: None)
    monkeypatch.setattr(provisional.redirect, "listener_available", lambda port: True)
    monkeypatch.setattr(provisional.redirect, "table_exists", lambda: existing[0])
    def nft(command, input_text=None):
        calls.append(command)
        if command == ["nft", "-f", "-"]:
            existing[0] = True
            return SimpleNamespace(returncode=1, stderr="partial failure")
        if command[:3] == ["nft", "delete", "table"]:
            existing[0] = False
        return SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr(provisional.redirect, "_run_nft", nft)
    with pytest.raises(Exception, match="failure"):
        provisional.apply(field_candidate())
    assert not existing[0]
    assert ["nft", "delete", "table", "ip", "ocpp_field_redirect"] in calls


def test_failed_nft_apply_does_not_delete_uncreated_table(monkeypatch):
    calls = []
    monkeypatch.setattr(provisional.redirect, "require_root", lambda: None)
    monkeypatch.setattr(provisional.redirect, "listener_available", lambda port: True)
    monkeypatch.setattr(provisional.redirect, "table_exists", lambda: False)
    def nft(command, input_text=None):
        calls.append(command)
        return SimpleNamespace(returncode=1 if command == ["nft", "-f", "-"] else 0, stderr="failure")
    monkeypatch.setattr(provisional.redirect, "_run_nft", nft)
    with pytest.raises(Exception, match="failure"):
        provisional.apply(field_candidate())
    assert not any("delete" in command for command in calls)


def test_unrelated_connected_charger_does_not_prove_target(monkeypatch):
    monkeypatch.setattr(first_contact.provisional, "local_syn_candidate", lambda *a, **k: field_candidate())
    monkeypatch.setattr(first_contact.provisional, "apply", lambda c: None)
    removed = []
    monkeypatch.setattr(first_contact.provisional, "remove", lambda: removed.append(True))
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", lambda *a: "capture")
    monkeypatch.setattr(first_contact.core, "_websocket_receipt", lambda *a, **k: field_receipt())
    monkeypatch.setattr(first_contact.core, "connected_chargers", lambda *a: ["OTHER-CHARGER"])
    with pytest.raises(RuntimeError, match="charger_connection_timeout"):
        first_contact.run_discovery(initial_evidence=FIELD_SYN, data_dir="data", connect_timeout=0)
    assert removed == [True]
