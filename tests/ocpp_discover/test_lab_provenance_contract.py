"""B2-D1 adversarial hermetic provenance matrix: memory-only fixtures."""
from dataclasses import FrozenInstanceError, replace
from enum import Enum

import pytest

from ocpp_discover import lab_provenance_contract as lab
from ocpp_discover.recovery_contract import Origin


def endpoint(**changes):
    fields = dict(
        interface="eth0",
        source_ip="192.168.129.182",
        captured_source_port=39812,
        destination_ip="8.8.8.8",
        destination_port=80,
        listen_port=9000,
        request_host="ocpp.example.invalid",
        request_path="/ocpp/CP-1",
        charger_id="CP-1",
    )
    fields.update(changes)
    return lab.EndpointScope(**fields)


def session(**changes):
    fields = dict(
        session_id="observed-session",
        peer_ip="192.168.129.182",
        peer_port=39812,
        charger_id="CP-1",
        event_id=24,
        event_session_id="observed-session",
        event_charger_id="CP-1",
        event_direction="in",
        watermark=23,
        matching_peer_sessions=1,
    )
    fields.update(changes)
    return lab.SessionBinding(**fields)


def evidence(origin=Origin.FIELD, *, scope=None, binding=None, **inventory):
    return lab.ProvenanceEvidence(
        origin=origin,
        endpoint=endpoint() if scope is None else scope,
        session=session() if binding is None else binding,
        **inventory,
    )


@pytest.mark.parametrize("address", [
    "198.18.0.1", "198.19.255.254", "192.0.2.1", "198.51.100.7",
    "203.0.113.10",
])
@pytest.mark.parametrize("origin", [Origin.FIELD, Origin.UNKNOWN, Origin.SYNTHETIC])
def test_reserved_lab_destination_is_never_field_candidate(address, origin):
    result = lab.assess_isolation(evidence(origin, scope=endpoint(destination_ip=address)))
    assert result.decision is lab.IsolationDecision.LAB_ONLY


@pytest.mark.parametrize("address", ["8.8.8.8", "10.42.0.1", "198.18.0.1"])
def test_synthetic_origin_is_lab_only_even_with_valid_session(address):
    result = lab.assess_isolation(evidence(
        Origin.SYNTHETIC, scope=endpoint(destination_ip=address)))
    assert result.decision is lab.IsolationDecision.LAB_ONLY


@pytest.mark.parametrize("inventory", [
    {"synthetic_charger_ids": ("CP-1",)},
    {"synthetic_source_ips": ("192.168.129.182",)},
    {"synthetic_destination_ips": ("8.8.8.8",)},
])
def test_explicit_lab_inventory_overrides_field_claim(inventory):
    assert lab.assess_isolation(evidence(**inventory)).decision is lab.IsolationDecision.LAB_ONLY


def test_imitated_physical_charger_identity_still_lab_only():
    fake = endpoint(charger_id="EVREADY-REAL", request_path="/ocpp/EVREADY-REAL")
    result = lab.assess_isolation(evidence(
        Origin.SYNTHETIC, scope=fake, binding=session(
            charger_id="EVREADY-REAL", event_charger_id="EVREADY-REAL")))
    assert result.decision is lab.IsolationDecision.LAB_ONLY


@pytest.mark.parametrize("origin", [Origin.UNKNOWN, Origin.FIELD])
def test_unknown_or_field_without_session_never_promotes(origin):
    value = lab.ProvenanceEvidence(origin, endpoint(), None)
    assert lab.assess_isolation(value).decision is lab.IsolationDecision.DENY_NEW_PERSISTENCE


def test_legacy_unknown_with_matching_event_is_still_denied():
    assert lab.assess_isolation(evidence(Origin.UNKNOWN)).decision is lab.IsolationDecision.DENY_NEW_PERSISTENCE


@pytest.mark.parametrize("field,value", [
    ("session_id", "other-session"),
    ("event_session_id", "other-session"),
    ("peer_ip", "192.168.129.183"),
    ("peer_port", 39813),
    ("charger_id", "CP-2"),
    ("event_charger_id", "CP-2"),
    ("event_direction", "out"),
    ("event_id", 23),
    ("watermark", 24),
    ("matching_peer_sessions", 0),
    ("matching_peer_sessions", 2),
])
def test_any_session_peer_event_or_reused_port_mismatch_is_denied(field, value):
    observed = evidence(binding=replace(session(), **{field: value}))
    result = lab.assess_isolation(observed)
    assert result.decision is lab.IsolationDecision.DENY_NEW_PERSISTENCE
    assert result.reason == "session_scope_or_event_mismatch"


def test_consistent_field_claim_is_only_review_not_authority():
    assert lab.assess_isolation(evidence()).decision is lab.IsolationDecision.FIELD_REVIEW_REQUIRED
    assert not hasattr(lab.IsolationDecision, "ALLOW_PERSIST")


@pytest.mark.parametrize("ip", ["10.42.0.1", "172.16.0.5", "192.168.129.9"])
def test_private_field_addresses_are_not_automatically_synthetic(ip):
    observed = evidence(scope=endpoint(destination_ip=ip))
    assert lab.assess_isolation(observed).decision is lab.IsolationDecision.FIELD_REVIEW_REQUIRED


def test_local_8888_scope_remains_representable():
    observed = evidence(scope=endpoint(
        destination_ip="10.42.0.1", destination_port=8888,
        request_host="10.42.0.1:8888"))
    assert lab.assess_isolation(observed).decision is lab.IsolationDecision.FIELD_REVIEW_REQUIRED


@pytest.mark.parametrize("changes", [
    {"interface": "bad/name"},
    {"source_ip": "127.0.0.1"},
    {"source_ip": "192.168.129.001"},
    {"destination_ip": "224.0.0.1"},
    {"captured_source_port": True},
    {"captured_source_port": 0},
    {"listen_port": 65536},
    {"destination_port": 9000},
    {"request_host": "bad host"},
    {"request_host": ""},
    {"request_path": "/ocpp/CP-2"},
    {"request_path": "/ocpp/CP-1?query=x"},
    {"request_path": "/ocpp/%43P-1"},
    {"request_path": "/ocpp/../CP-1"},
    {"request_path": "/ocpp//CP-1"},
    {"request_path": "/ocpp/CP-1/"},
    {"charger_id": ""},
])
def test_invalid_or_ambiguous_endpoint_rejected(changes):
    with pytest.raises(ValueError):
        endpoint(**changes)


@pytest.mark.parametrize("changes", [
    {"peer_ip": "not-an-ip"},
    {"peer_port": 0},
    {"event_id": True},
    {"event_id": 0},
    {"watermark": -1},
    {"matching_peer_sessions": True},
    {"matching_peer_sessions": -1},
    {"session_id": ""},
])
def test_malformed_session_binding_rejected(changes):
    with pytest.raises(ValueError):
        session(**changes)


@pytest.mark.parametrize("inventory", [
    {"synthetic_charger_ids": ["CP-1"]},
    {"synthetic_destination_ips": ["8.8.8.8"]},
    {"synthetic_source_ips": ["192.168.129.182"]},
    {"synthetic_charger_ids": ("CP-1", "CP-1")},
    {"synthetic_destination_ips": ("8.8.8.8", "8.8.8.8")},
    {"synthetic_source_ips": ("not-ip",)},
])
def test_mutable_or_ambiguous_lab_inventories_rejected(inventory):
    with pytest.raises(ValueError):
        evidence(**inventory)


def test_inputs_are_deeply_immutable():
    observed = evidence(synthetic_charger_ids=("CP-test",))
    with pytest.raises(FrozenInstanceError):
        observed.origin = Origin.SYNTHETIC
    with pytest.raises(FrozenInstanceError):
        observed.endpoint.source_ip = "198.18.0.1"
    with pytest.raises(FrozenInstanceError):
        observed.session.event_id = 999
    assert type(observed.synthetic_charger_ids) is tuple
    assert not hasattr(observed, "__dict__")
    assert not hasattr(observed.endpoint, "__dict__")


def test_origin_string_and_forged_evidence_rejected():
    with pytest.raises(ValueError):
        evidence(origin="field")
    with pytest.raises(ValueError):
        lab.assess_isolation({"origin": "field"})
    with pytest.raises(ValueError):
        lab.ProvenanceEvidence(Origin.FIELD, endpoint(), session(), synthetic_charger_ids=("CP-1", None))


def test_pure_contract_has_no_host_io_or_persistence_dependencies():
    for forbidden in ("os", "subprocess", "socket", "sqlite3", "pathlib", "requests"):
        assert forbidden not in vars(lab)
    assert set(lab.IsolationDecision) == {
        lab.IsolationDecision.LAB_ONLY,
        lab.IsolationDecision.DENY_NEW_PERSISTENCE,
        lab.IsolationDecision.FIELD_REVIEW_REQUIRED,
    }


def test_field_reviewer_claims_cannot_create_persistence_permission():
    cases = (
        evidence(),
        evidence(Origin.UNKNOWN),
        evidence(Origin.SYNTHETIC),
        evidence(scope=endpoint(destination_ip="198.18.0.1")),
    )
    for candidate in cases:
        assessment = lab.assess_isolation(candidate)
        assert assessment.decision.value != "allow_persist"
        assert isinstance(assessment.decision, Enum)

@pytest.mark.parametrize("decision", [
    "allow_persist", "field_review_required", "", None, True, 0, object(),
])
def test_isolation_assessment_rejects_forged_decisions(decision):
    with pytest.raises(ValueError, match="invalid_isolation_decision"):
        lab.IsolationAssessment(decision, "forged")


@pytest.mark.parametrize("reason", ["", None, 0, True, (), object()])
def test_isolation_assessment_rejects_invalid_reasons(reason):
    with pytest.raises(ValueError, match="invalid_isolation_reason"):
        lab.IsolationAssessment(lab.IsolationDecision.LAB_ONLY, reason)


@pytest.mark.parametrize("origin,expected", [
    (Origin.SYNTHETIC, lab.IsolationDecision.LAB_ONLY),
    (Origin.UNKNOWN, lab.IsolationDecision.DENY_NEW_PERSISTENCE),
    (Origin.FIELD, lab.IsolationDecision.FIELD_REVIEW_REQUIRED),
])
def test_assess_isolation_keeps_all_three_typed_decisions(origin, expected):
    result = lab.assess_isolation(evidence(origin))
    assert type(result) is lab.IsolationAssessment
    assert type(result.decision) is lab.IsolationDecision
    assert result.decision is expected
    assert type(result.reason) is str and result.reason


def test_isolation_assessment_accepts_valid_typed_value():
    result = lab.IsolationAssessment(lab.IsolationDecision.LAB_ONLY, "tested")
    assert result.decision is lab.IsolationDecision.LAB_ONLY
    assert result.reason == "tested"
