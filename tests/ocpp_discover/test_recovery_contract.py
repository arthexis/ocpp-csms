"""B2-C hermetic fault matrix. Simulated interruptions are not field tests."""
from pathlib import Path

import pytest

from ocpp_discover.recovery_contract import (
    Artifact, Assessment, Decision, Interruption, Origin, Ownership,
    Phase, Snapshot, State, assess,
)


A = Artifact(Ownership.OWNED, "a" * 64)
B = Artifact(Ownership.OWNED, "b" * 64)
X = Artifact(Ownership.ABSENT)
F = Artifact(Ownership.FOREIGN)
U = Artifact(Ownership.UNKNOWN)


def sample(live=X, fragment=X, receipt=X, phase=Phase.NONE,
           origin=Origin.UNKNOWN, session=False, interruption=Interruption.NONE):
    return Snapshot(live, fragment, receipt, phase, origin, session, interruption)


def test_absent_is_observation_only():
    assert assess(sample()) == Assessment(State.ABSENT, Decision.OBSERVE_ONLY, "no_adaptation")


@pytest.mark.parametrize("phase", [Phase.NONE, Phase.COMPLETE])
def test_consistent_owned_persistent_state_is_only_preserved(phase):
    result = assess(sample(A, A, A, phase))
    assert result.state is State.PERSISTENT
    assert result.decision is Decision.PRESERVE



@pytest.mark.parametrize("phase", [Phase.NONE, Phase.COMPLETE])
@pytest.mark.parametrize("session", [False, True])
def test_known_synthetic_complete_evidence_never_preserved(phase, session):
    # All three separately marked owned + identical digest is insufficient
    # for known synthetic origin, regardless of claimed session proof.
    result = assess(sample(A, A, A, phase=phase, origin=Origin.SYNTHETIC,
                           session=session))
    assert result == Assessment(State.UNCERTAIN, Decision.OPERATOR_REQUIRED,
                                "synthetic_origin_cannot_be_persistent")


@pytest.mark.parametrize("phase", [Phase.NONE, Phase.COMPLETE])
def test_legacy_unknown_origin_preserves_verified_existing_artifacts(phase):
    # Historical artifacts may predate an origin field. Do not erase their
    # read-only preservation status; ownership is a separate trusted input.
    result = assess(sample(A, A, A, phase=phase, origin=Origin.UNKNOWN))
    assert result.state is State.PERSISTENT
    assert result.decision is Decision.PRESERVE


@pytest.mark.parametrize("phase", [Phase.NONE, Phase.COMPLETE])
@pytest.mark.parametrize("different", ["live", "fragment", "receipt"])
def test_canonical_scope_digest_disagreement_never_preserves(phase, different):
    # Digests are assumed to identify canonical *scope*, not source file
    # bytes. Different supplied scopes can never describe one adaptation.
    artifacts = {"live": A, "fragment": A, "receipt": A}
    artifacts[different] = B
    result = assess(sample(**artifacts, phase=phase, origin=Origin.FIELD))
    assert result == Assessment(State.UNCERTAIN, Decision.OPERATOR_REQUIRED,
                                "contradictory_artifact_identity")


@pytest.mark.parametrize("phase", [Phase.NONE, Phase.COMPLETE])
def test_missing_independent_ownership_is_not_supplied_by_equal_digests(phase):
    # Matching known peers cannot certify an unverified third artifact.
    result = assess(sample(A, A, U, phase=phase, origin=Origin.FIELD))
    assert result.state is State.UNCERTAIN
    assert result.decision is Decision.OPERATOR_REQUIRED

def test_explicit_provisional_is_not_automatically_removed():
    assert assess(sample(A, phase=Phase.PROVISIONAL)) == Assessment(
        State.PROVISIONAL, Decision.OPERATOR_REQUIRED, "provisional_hold"
    )


def test_proven_field_session_still_cannot_auto_promote():
    result = assess(sample(A, phase=Phase.PROVEN, origin=Origin.FIELD, session=True))
    assert result.state is State.PROVEN
    assert result.decision is Decision.OPERATOR_REQUIRED


@pytest.mark.parametrize("origin,session", [
    (Origin.SYNTHETIC, True), (Origin.UNKNOWN, True),
    (Origin.FIELD, False), (Origin.SYNTHETIC, False),
])
def test_synthetic_or_missing_proof_never_promotes(origin, session):
    result = assess(sample(A, phase=Phase.PROVEN, origin=origin, session=session))
    assert result.state is State.UNCERTAIN
    assert result.decision is Decision.OPERATOR_REQUIRED


@pytest.mark.parametrize("interruption", [
    Interruption.SIGKILL, Interruption.CRASH, Interruption.REBOOT,
    Interruption.WRITE_FAILURE, Interruption.ROLLBACK_FAILURE,
])
def test_lost_journal_and_orphan_live_table_fail_closed(interruption):
    result = assess(sample(A, interruption=interruption))
    assert result.state is State.UNCERTAIN
    assert result.decision is Decision.OPERATOR_REQUIRED


@pytest.mark.parametrize("interruption", [
    Interruption.SIGKILL, Interruption.CRASH, Interruption.REBOOT,
    Interruption.WRITE_FAILURE, Interruption.ROLLBACK_FAILURE,
])
@pytest.mark.parametrize("phase", [Phase.COMMITTING, Phase.RECONCILING])
@pytest.mark.parametrize("fragment,receipt", [
    (X, X), (A, X), (X, A), (A, A),
])
def test_nonatomic_transition_requires_operator(interruption, phase, fragment, receipt):
    result = assess(sample(A, fragment, receipt, phase,
                           interruption=interruption))
    assert result.state is State.RECONCILING
    assert result.decision is Decision.OPERATOR_REQUIRED


@pytest.mark.parametrize("live,fragment,receipt", [
    (F, X, X), (X, F, X), (X, X, F), (F, A, A),
])
def test_foreign_rules_are_never_touched(live, fragment, receipt):
    result = assess(sample(live, fragment, receipt, phase=Phase.RECONCILING))
    assert result == Assessment(State.FOREIGN, Decision.OPERATOR_REQUIRED,
                                "foreign_do_not_touch")


@pytest.mark.parametrize("live,fragment,receipt", [
    (U, X, X), (A, U, A), (A, A, U),
])
def test_unknown_ownership_requires_operator(live, fragment, receipt):
    result = assess(sample(live, fragment, receipt))
    assert result.state is State.UNCERTAIN


def test_mismatched_live_and_durable_identity_fails_closed():
    result = assess(sample(B, A, A, phase=Phase.RECONCILING,
                           interruption=Interruption.ROLLBACK_FAILURE))
    assert result.reason == "contradictory_artifact_identity"
    assert result.decision is Decision.OPERATOR_REQUIRED


def test_reboot_with_missing_live_table_is_not_automatic_recovery():
    result = assess(sample(X, A, A, phase=Phase.COMPLETE,
                           interruption=Interruption.REBOOT))
    assert result.state is State.UNCERTAIN


@pytest.mark.parametrize("phase", [
    Phase.PREPARING, Phase.PROVISIONAL, Phase.PROVEN, Phase.COMPLETE,
])
def test_unexpected_partial_fragment_is_uncertain(phase):
    assert assess(sample(X, A, X, phase)).state is State.UNCERTAIN


def test_unknown_phase_is_uncertain_even_if_artifacts_match():
    assert assess(sample(A, A, A, Phase.UNKNOWN)).state is State.UNCERTAIN


def test_invalid_input_is_rejected():
    with pytest.raises(ValueError):
        Artifact("owned", "a" * 64)
    with pytest.raises(ValueError):
        Artifact(Ownership.OWNED, "not-a-sha256")
    with pytest.raises(ValueError):
        Artifact(Ownership.ABSENT, "a" * 64)
    with pytest.raises(ValueError):
        Snapshot(X, X, X, phase="complete")
    with pytest.raises(ValueError):
        Snapshot(X, X, X, session_proven=1)
    with pytest.raises(ValueError):
        assess({"live": A})


def test_contract_has_no_effectful_recovery_interface():
    from ocpp_discover import recovery_contract
    source = Path(recovery_contract.__file__).read_text()
    for forbidden in ("import os", "import subprocess", "import sqlite3",
                      "import socket", "systemctl", "_run_nft", "os.replace",
                      "os.unlink", "Path(", "open("):
        assert forbidden not in source
    assert set(Decision) == {
        Decision.OBSERVE_ONLY, Decision.PRESERVE, Decision.OPERATOR_REQUIRED
    }
