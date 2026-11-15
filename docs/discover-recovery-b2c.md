# Stage B2-C — Pure recovery states and fault decisions (#355)

**PRE-READY, offline only.** This stage adds an immutable recovery
classification contract and hermetic tests. It does not inspect or alter
any real host, table, receipt, fragment, SQLite database, service or interface.
It deliberately does not wire recovery into Discover startup, deploy or
operational reconciliation. Routed TCP/80 remains default-off.

## Authority and ownership boundary

The new recovery_contract module receives an external immutable Snapshot
comprising the live Discover nftables table, Discover-owned persistent
fragment and discovered.json receipt. Each has an ownership state (absent,
owned, foreign or unknown) and, when independently verified as owned,
a SHA-256-shaped content correlation digest.

**Equal digests, table names, file paths or receipts never establish
ownership.** An independently trusted read-only evidence collector would
have to establish exact ownership and rule scope. No such collector,
durable recovery journal or authorization capability is created by B2-C.
The classifier treats ownership evidence as an *input*, not as a newly
invented proof. It never grants permission to add, replace or remove rules.

## Canonical semantic adaptation identity v1 (specification, not code)

An Artifact.digest represents SHA-256 of the **same normalized redirect
scope**, not SHA-256 of the different raw byte encodings of an nftables
table, a configuration fragment and a JSON receipt. Before setting
ownership=owned, a future independently trusted read-only observer must
verify that it controls the **entire exact rule scope** and that no extra
or contradictory rules or unknown ownership are present. Digest agreement
never substitutes for this ownership check.

For future interoperable implementations, scope v1 is the following
typed record, normalized by parsing each representation into the same
semantic fields:

- version: integer 1
- family: literal string ip
- table: literal string ocpp_field_redirect
- ingress_interface: exact Linux interface name (iifname)
- source_ipv4: canonical IPv4 source (ip saddr)
- destination_ipv4: deduplicated ascending numeric IPv4 array (ip daddr)
- tcp_destination_port: integer original destination TCP port (tcp dport)
- redirect_port: integer local TCP redirect port (redirect to :port)

The future observer must reject absent/ambiguous fields, unexpected
additional rules, invalid IPs/ports, differing scopes and any unverified
ownership. If implemented later, the digest serialization is UTF-8 JSON
with precisely those keys, keys sorted lexicographically, no insignificant
whitespace, standard decimal integers and canonical dotted-quad IPv4
addresses; hash those serialized bytes. **This patch neither computes
nor verifies such hashes**, and it does not mint trust or capabilities.
Host/path, charger ID, WebSocket identity and proof of a fresh OCPP
session are not present in the live nftables rule and **cannot** be inferred
from this digest. They require separate trusted evidence and session
correlation before any future field promotion.

A known Origin.SYNTHETIC is UNCERTAIN/OPERATOR_REQUIRED, never
PERSISTENT/PRESERVE in Phase.NONE or Phase.COMPLETE, even when all
three artifacts are labeled owned with identical digests. Historical
Origin.UNKNOWN retains the read-only preservation classification only
when independent ownership and scope consistency have already been
established; that classification is **not** permission to activate or
persist a field adaptation.

The optional phase is also externally supplied: none, preparing,
provisional, proven, committing, reconciling, complete or unknown.
No journal is written in this stage. The interruption label (SIGKILL,
crash, simulated reboot, write failure, incomplete rollback) is used
only to describe injected test snapshots.

## State and decision matrix

| State | Evidence | Result |
| --- | --- | --- |
| absent | All artifacts absent, no active phase | Observe only |
| provisional | Only live table owned; explicit provisional phase | Operator required; do not auto-delete |
| proven | Only live table owned; explicit proven phase plus field session evidence | Operator required; never auto-persist |
| persistent | Three independently owned artifacts with matching **canonical semantic scope** digest, no intermediate phase, and origin **not explicitly synthetic** | Preserve only |
| reconciling | Explicit committing or reconciling phase with noncontradictory known evidence | Operator required; no replay |
| uncertain | Unknown ownership/phase, contradiction, orphan, missing durable file or unsupported phase | Operator required |
| foreign | Any foreign artifact | Operator required; never touch foreign rules |

Every decision is advisory: OBSERVE_ONLY, PRESERVE or OPERATOR_REQUIRED.
No action performs cleanup, applies nftables, promotes receipts, changes
a service or starts a retry loop. If journal evidence is lost, even an
apparently familiar live table must not be silently claimed or removed.

## Current non-atomic failure windows

- provisional.apply creates a live table before session proof; SIGTERM
  cleanup does not prove protection against SIGKILL or power loss
- handoff._promote_persistent_adaptation writes the fragment and then
  discovered.json, with exception rollback but no multi-artifact atomicity
- reconcile.reconcile changes the live table before committing persistent
  fragment and receipt; an interrupted or failed rollback may leave
  contradictory state
- file fsync and atomic replacement alone do not establish crash-safe
  durability across a table, two files and parent directories
- startup warnings do not certify live table provenance or physical topology

Hermetic pytest inputs simulate every interruption type before/during
commit or reconciliation, an orphan table, foreign rules, missing live
table after reboot, mismatched artifact digests and unproven synthetic
session evidence. They do not kill real processes or reboot an appliance.

## Boundaries for later work

Future separately authorized implementation would need a trustworthy
read-only observation adapter, durable transition journal, ownership
attestation and a reviewed recovery executor before any automatic
nftables action is permitted. No such mechanism is in scope here.

B2-D owns synthetic identity/destination nonpromotion and complete lab
isolation. Stage C owns real EV Ready physical network, gateway,
routing, forwarding, WebSocket/OCPP evidence and field reboot tests.
A fabricated field-origin flag or session proof cannot authorize any
real persistence in B2-C. Explicitly synthetic origin never yields a
preserved field adaptation, even if a synthetic snapshot claims completed
persistence. The existing CSMS versioned storage, SQLite,
transactions, evidence, receipts, local 8888 opt-in and deployed firewall
state are untouched.
