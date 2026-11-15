"""Pure B2-C recovery assessment; no host reads, writes or execution.

Artifact ownership must be independently established by a future trusted
observer. A matching name, file or SHA-256 digest is not ownership proof.
The digest correlates a canonical *semantic redirect scope*, never raw bytes
of dissimilar nftables, fragment and JSON receipt representations. Canonical
scope v1 is specified in docs/discover-recovery-b2c.md; no adapter exists here.
"""
from dataclasses import dataclass
from enum import Enum
import re


class Ownership(str, Enum):
    ABSENT = "absent"
    OWNED = "owned"
    FOREIGN = "foreign"
    UNKNOWN = "unknown"


class Phase(str, Enum):
    NONE = "none"
    PREPARING = "preparing"
    PROVISIONAL = "provisional"
    PROVEN = "proven"
    COMMITTING = "committing"
    RECONCILING = "reconciling"
    COMPLETE = "complete"
    UNKNOWN = "unknown"


class Origin(str, Enum):
    FIELD = "field"
    SYNTHETIC = "synthetic"
    UNKNOWN = "unknown"


class Interruption(str, Enum):
    NONE = "none"
    SIGKILL = "sigkill"
    CRASH = "crash"
    REBOOT = "reboot"
    WRITE_FAILURE = "write_failure"
    ROLLBACK_FAILURE = "rollback_failure"


class State(str, Enum):
    ABSENT = "absent"
    PROVISIONAL = "provisional"
    PROVEN = "proven"
    PERSISTENT = "persistent"
    RECONCILING = "reconciling"
    UNCERTAIN = "uncertain"
    FOREIGN = "foreign"


class Decision(str, Enum):
    OBSERVE_ONLY = "observe_only"
    PRESERVE = "preserve"
    OPERATOR_REQUIRED = "operator_required"


@dataclass(frozen=True)
class Artifact:
    """Digest correlates canonical semantic redirect scope v1, NOT raw bytes.

    A separately trusted observer must independently prove ownership and
    normalize each representation. No such observer is provided here.
    """
    ownership: Ownership
    digest: str | None = None

    def __post_init__(self):
        if type(self.ownership) is not Ownership:
            raise ValueError("invalid_artifact_ownership")
        if self.ownership is Ownership.OWNED:
            if type(self.digest) is not str or re.fullmatch(r"[0-9a-f]{64}", self.digest) is None:
                raise ValueError("invalid_owned_digest")
        elif self.digest is not None:
            raise ValueError("untrusted_artifact_digest")


@dataclass(frozen=True)
class Snapshot:
    """Immutable evidence; no adapter or journal is implemented in B2-C."""
    live: Artifact
    fragment: Artifact
    receipt: Artifact
    phase: Phase = Phase.NONE
    origin: Origin = Origin.UNKNOWN
    session_proven: bool = False
    interruption: Interruption = Interruption.NONE

    def __post_init__(self):
        if (any(type(a) is not Artifact for a in (self.live, self.fragment, self.receipt))
                or type(self.phase) is not Phase or type(self.origin) is not Origin
                or type(self.interruption) is not Interruption
                or type(self.session_proven) is not bool):
            raise ValueError("invalid_recovery_snapshot")


@dataclass(frozen=True)
class Assessment:
    state: State
    decision: Decision
    reason: str


def _uncertain(reason: str) -> Assessment:
    return Assessment(State.UNCERTAIN, Decision.OPERATOR_REQUIRED, reason)


def assess(snapshot: Snapshot) -> Assessment:
    """Return advisory classification only; never authorize nft or persistence."""
    if type(snapshot) is not Snapshot:
        raise ValueError("invalid_recovery_snapshot")
    assets = (snapshot.live, snapshot.fragment, snapshot.receipt)
    ownership = tuple(a.ownership for a in assets)
    if Ownership.FOREIGN in ownership:
        return Assessment(State.FOREIGN, Decision.OPERATOR_REQUIRED, "foreign_do_not_touch")
    if Ownership.UNKNOWN in ownership or snapshot.phase is Phase.UNKNOWN:
        return _uncertain("unknown_ownership_or_phase")
    if len({a.digest for a in assets if a.ownership is Ownership.OWNED}) > 1:
        return _uncertain("contradictory_artifact_identity")

    empty = ownership == (Ownership.ABSENT,) * 3
    complete = ownership == (Ownership.OWNED,) * 3
    provisional = ownership == (Ownership.OWNED, Ownership.ABSENT, Ownership.ABSENT)
    # Known synthetic evidence is never a persistent field adaptation, even
    # if three independently labeled artifacts share a correlation digest.
    # Historical UNKNOWN origin remains readable when ownership is verified.
    if (complete and snapshot.origin is Origin.SYNTHETIC
            and snapshot.phase in (Phase.NONE, Phase.COMPLETE)):
        return _uncertain("synthetic_origin_cannot_be_persistent")
    if snapshot.phase is Phase.NONE:
        if empty:
            return Assessment(State.ABSENT, Decision.OBSERVE_ONLY, "no_adaptation")
        if complete:
            return Assessment(State.PERSISTENT, Decision.PRESERVE, "consistent_existing")
        return _uncertain("orphan_without_phase_evidence")
    if snapshot.phase is Phase.COMPLETE:
        if complete:
            return Assessment(State.PERSISTENT, Decision.PRESERVE, "consistent_completed")
        return _uncertain("complete_artifacts_mismatch")
    if snapshot.phase is Phase.PROVISIONAL and provisional:
        return Assessment(State.PROVISIONAL, Decision.OPERATOR_REQUIRED, "provisional_hold")
    if snapshot.phase is Phase.PROVEN and provisional:
        if snapshot.session_proven and snapshot.origin is Origin.FIELD:
            return Assessment(State.PROVEN, Decision.OPERATOR_REQUIRED, "field_proof_not_persist_authority")
        return _uncertain("unproven_or_synthetic_session")
    if snapshot.phase in (Phase.COMMITTING, Phase.RECONCILING):
        return Assessment(State.RECONCILING, Decision.OPERATOR_REQUIRED, "nonatomic_transition_hold")
    return _uncertain("phase_artifact_mismatch")
