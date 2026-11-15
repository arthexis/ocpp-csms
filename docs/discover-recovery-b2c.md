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
| persistent | Live table, fragment and receipt independently owned, matching digest; no intermediate phase | Preserve |
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
real persistence in B2-C. The existing CSMS versioned storage, SQLite,
transactions, evidence, receipts, local 8888 opt-in and deployed firewall
state are untouched.
