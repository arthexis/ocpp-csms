# Stage B2-D1 — Pure lab provenance and synthetic nonpromotion contract (#355)

**Draft implementation, hermetic only.** This stage is an advisory data contract
and tests. It adds no production enforcement, no trusted physical observer,
no authority token, no persistent journal, and no route from an assessment to
a privileged action. The absence of a lab marker is not proof of field origin.

## Scope and typed evidence

`ocpp_discover.lab_provenance_contract` reuses the existing
`recovery_contract.Origin` enum: `FIELD`, `SYNTHETIC`, `UNKNOWN`.
Its immutable, slots-based inputs are:

- `EndpointScope`: one exact ingress interface, IPv4 source and captured
  TCP source port, IPv4 destination and original TCP destination port,
  local listener port, plaintext WebSocket Host, path and charger identity.
  The identity is the literal final path component. Ambiguous paths,
  encodings, query strings and multi-destination receipts are outside
  this deliberately narrow contract.
- `SessionBinding`: asserted session ID, exact peer IP/port and charger,
  one post-watermark inbound OCPP event, event-session linkage and the
  count of sessions matching the captured peer tuple. A count other than
  exactly one is inconclusive due to possible port reuse.
- `ProvenanceEvidence`: declared origin, exact endpoint/session, and
  immutable explicit inventories of synthetic charger IDs, source IPs
  and destination IPs. These inventories provide additional **denials**;
  they cannot create trust when empty.
- `IsolationAssessment`: decision and stable reason. It cannot grant a
  field write, a network change, or physical acceptance.

The implementation uses scalar fields and immutable tuples; it does not
embed `RedirectReceipt`'s mutable lists or retain mutable caller objects.
The API checks exact Python types and canonical IPv4 addresses. A `FIELD`
label, matching event ID, session count, Host/path, ownership string or
digest is still caller-supplied data, **not independently verified proof**.
Python frozen objects are a coding invariant, not a security boundary
against arbitrary privileged Python code.

## Decision matrix

| Evidence | Decision | Meaning |
| --- | --- | --- |
| `Origin.SYNTHETIC`, regardless of apparent session success | `LAB_ONLY` | No field promotion |
| Reserved lab destination or explicit synthetic identity/source/destination | `LAB_ONLY` | Deny field promotion, even if origin says FIELD |
| `Origin.UNKNOWN` | `DENY_NEW_PERSISTENCE` | Keep historical assessment separate |
| FIELD with absent, ambiguous, reused or mismatched session evidence | `DENY_NEW_PERSISTENCE` | Session evidence insufficient |
| FIELD with internally consistent exact-scope event evidence | `FIELD_REVIEW_REQUIRED` | **Review only**, never authorization |

Reserved lab destinations are `198.18.0.0/15`, `192.0.2.0/24`,
`198.51.100.0/24`, and `203.0.113.0/24`. These address-space
checks are **deny-only**. RFC 1918 private addresses are not universally
synthetic; real chargers may use private IP addresses. A laboratory using
other addresses or a realistic charge point ID must declare synthetic
origin and register its precise endpoints. The contract cannot detect
undisclosed simulations that mimic field traffic; it also never grants
write authority based on their absence.

The inbound OCPP event check requires event ID greater than its pre-redirect
watermark, inbound direction, the same charger and session ID, the same
captured IP/port, and exactly one matching peer session. These data fields
mirror useful first-contact correlation semantics but do not read SQLite
or attest physical peer exclusivity. No BootNotification is presumed merely
from the existence of an inbound event.

## Exact limits of B2-D1

Three new files only: this document, the pure source module and hermetic
tests. Existing `RedirectReceipt`, `discovered.json` **v1**, CSMS schema,
SQLite evidence, `recovery_contract`, `service`, `handoff`,
`reconcile`, `redirect`, Ansible and systemd remain untouched. No
nftables, capture, socket, filesystem read/write, subprocess or network
operation is invoked by the new module. Tests use only in-memory fixtures.

The local TCP/8888 operator opt-in is preserved. Routed TCP/80 remains
default-off because B2-B2 cannot establish physical peer exclusivity.
This contract does not change any deployment or running service.

## Boundaries for the subsequent stages

**B2-D2, separately authorized:** acquire trustworthy session/provenance
evidence; reject synthetic/unknown new promotions before side effects in
`service._wait_then_discover`, `handoff.cutover`,
`handoff._promote_persistent_adaptation` and `reconcile.reconcile`;
protect the final persistence writers; preserve historical UNKNOWN receipts
without treating their read-only B2-C classification as new-write authority.
Review the exact placement of guards before any old service stop, live
nftables replacement, fragment write, receipt write or rollback.

**Stage C, separately authorized:** physical EV Ready interface isolation,
actual gateway/routing/forwarding, observable OCPP session and events,
reconnect and reboot. Neither synthetic tests nor this module establish
field acceptance or allow activation of routed interception.

B2-C's `PRESERVE` means preserve existing independently verified artifacts
only; B2-D1's `FIELD_REVIEW_REQUIRED` means seek independent verification,
not permission to persist. No function in this contract returns
`ALLOW_PERSIST`.
