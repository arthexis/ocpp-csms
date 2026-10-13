# Chunk A — Evidence-first OCPP ingestion

Refs #244. This chunk deliberately does not remove the recovery worker or change control guards.

## Contract
- An incomplete or inconsistent historical transaction cannot prevent a valid new StartTransaction from receiving a fresh, non-colliding transaction ID and OCPP response.
- A repeated StartTransaction request within the existing retry window retains its original ID, including across archive/SQLite partial writes.
- Every received MeterValues/StopTransaction is recorded as raw OCPP evidence before attempting derived/archive updates; an absent start is not an error.
- For known historical transaction IDs, attach compatible messages; preserve conflicting messages in transactions-unresolved, retaining the original charger, ID, connector and payload.
- An unresolved collision must not update the original transaction's meter totals, stop state or activity timestamp.
- Do not invent a StopTransaction or mark an incomplete transaction ended as part of ingestion.
- RFID policy remains unchanged. Storage failure must be visible in diagnostics; no claim of durable acceptance when persistence failed.

## Implementation order
1. Add characterization tests for multiple new starts on a connector with an older unclosed transaction; duplicate start; MeterValues before start; StopTransaction without start; late stop; foreign charger ID collision; connector mismatch; partial SQLite/archive failure.
2. Decouple StartTransaction ID allocation from old active-state checks, and ensure archive IDs cannot be reused when SQLite knows an ID or another archive already holds it.
3. Make MeterValues and StopTransaction ingestion tolerate missing/legacy records while preserving raw events, and keep unresolved conflicts separate.
4. Verify no projection mutation for a conflicting transaction; keep response semantics and idempotence explicit.
5. Consolidate cross-restart ingestion regression tests; run Python 3.11, Python 3.13 and general CI; stop for review before Chunk B.

## Exclusions
Live connector control guards, recovery-loop removal, CLI/report status redesign, schema migration, exporter compatibility.

## A1–A5 verification coverage
- New starts remain accepted with incomplete older transactions; SQLite-only IDs are reserved when readable. Locked SQLite uses a collision-resistant fallback, not an absolute uniqueness guarantee against unknown SQLite-only IDs.
- MeterValues without a start are retained without creating a synthetic start; connector-only readings remain raw events.
- Cross-charge-point, cross-connector and earlier-than-start conflicts are preserved separately and do not change the original transaction's derived state.
- A genuine late StopTransaction completes only the matching transaction; identical retries do not rewrite its archive, and conflicting later stops remain unresolved.
- Integrated regression tests cover orphan evidence, a restart, subsequent starts and late stops without inferred transitions.
