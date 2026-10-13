# Export cursor contract (issue #224, chunk A)

This document distinguishes **current behavior** from the **proposed producer extension**. Nothing in the proposed section is available to consumers until chunk B implements it and chunk C verifies it.

## Verified current `ocpp-csms/export/v1` behavior

- The top-level JSON result has schema `ocpp-csms/export/v1`; `data` includes `source_id`, `cursor`, `events`, `energy`, `transactions`, and `status`.
- `source_id` is a persisted UUID in SQLite `appliance_metadata`, identifying the data store, not an individual CP. Namespace event IDs and transaction IDs by source. A copied/restored database may retain its source ID, so clones must not be treated as independent producers.
- `cursor.after` is an **exclusive** lower bound on SQLite OCPP event IDs. Events are ascending by ID and bounded by `limit`. `cursor.next` is the last included event ID, or the input `after` on an empty page. `cursor.more` reflects whether an additional event was visible at query time. IDs need not be contiguous. `after >= 0`, `limit >= 1`; there is currently no enforced upper limit.
- Runtime events are not included. Energy samples are derived from MeterValues events in the selected page. A repeated page can return the same event identities but need not be byte-identical.
- Transaction items are **current archive snapshots** for IDs referenced by that event page. The event query, archive reads and status reads are **not atomic**. Archive-only corrections can be missed entirely by event pagination. An empty event page does not imply unchanged transactions.
- Existing transaction `started_at` and `stopped_at` prefer receive/archive times. They are not guaranteed charger-reported event timestamps. `energy_wh` is watt-hours; absent readings must not be converted into invented zero energy.
- The current producer does not expose a consistent snapshot, event high-watermark, durable transaction-change cursor, reset epoch or correction completeness guarantee. A cursor ahead of the maximum can currently return an empty page without an error.

## Chunk B design: durable independent transaction changes

Introduce an **independent, monotonically increasing transaction change ID** with an exclusive `after` cursor and deterministic ascending pagination. Each change carries the stable identity `(source_id, transaction_id)`, change ID, UTC observation time and canonical content fingerprint. A consumer deduplicates by change ID and **upserts** a transaction revision; it must never sum repeated snapshots.

Every authoritative archive mutation, including recovery, inferred stop, late stop, reconciliation and archive-only correction, must produce a durable change. Audit all writers before choosing the hook. SQLite and JSON files cannot be committed in one atomic transaction: choose and test a write-ahead/outbox or repair/reconciliation strategy that closes the crash window; do not promise impossible cross-store atomicity. If deletions are supported, define tombstones. An event cursor must never substitute for this feed.

Add timezone-aware UTC `exported_at` and an event high-watermark captured at a documented boundary. A high-watermark is **not** a consistent snapshot of SQLite, JSON archives and status. Bound event pagination against that watermark only if implementation explicitly does so.

Preserve existing v1 timestamp fields and add separate nullable charger event and CSMS receive times for start/stop and meter samples where evidence exists. Invalid or missing event timestamps remain null, not substituted with receive times. Document UTC normalization and offset handling. Add explicit, evidence-based quality flags for active, incomplete, missing-meter, recovered/inferred and corrected transactions; preserve nullable `energy_wh` in Wh.

## Source resets, retries and compatibility

A changed `source_id` is a new namespace. A cursor greater than the source's current high-watermark requires an explicit reset/gap outcome in the extended protocol. Numeric holes within the ID sequence do not by themselves prove lost events. Restoring an earlier backup with the **same** source ID requires a durable generation/epoch or an explicitly documented reset procedure; source ID alone cannot detect it.

Event and change cursors advance independently. Retrying an `after` value is safe for event/change identity but not a guarantee of immutable transaction snapshots. Specify pagination limits, replay, error behavior, reset and retention before promising durable replication.

Additive fields may remain in `export/v1` only when existing meanings are unchanged. Otherwise introduce `export/v2` and a deliberate consumer transition. The obsolete forwarder was removed in #240; there is no forwarder compatibility or migration requirement. Validate against `arthexis/ocpp-reports` consumer fixtures, without importing Reports into CSMS.

## Chunk A / B / C acceptance gates

- **A:** Pin existing event cursor semantics and input validation in pytest; record current non-atomicity and agree on independent change-feed design. No producer behavior change.
- **B:** Implement durable changes, observation metadata, timestamp provenance and quality markers; prove correction-only changes and crash recovery.
- **C:** Add sanitized fixtures for active-to-completed, repeated/out-of-order events, archive-only corrections, empty pages, reset, missing meters, timezone offsets and malformed data; validate OCPP Reports ingestion and required CI.

## Chunk B read-only boundary (implementation in progress)

The export path reads the transaction-change SQLite journal with a read-only connection and never creates it or reconciles archives. If the journal has not been initialized, the change feed is empty. The producer archives each committed transaction mutation and reconciles unjournaled archive revisions during `TransactionArchive` initialization, not during export. A crash between the JSON replacement and journal insertion is recovered when the producer next initializes; until that recovery occurs, an export can temporarily lag the archive. This is not a cross-store atomic snapshot guarantee. This strategy does not reconstruct intermediate revisions lost in the crash window and needs explicit operational verification in chunk C.
