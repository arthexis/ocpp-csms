# Chunk A — recovery writer and export audit (#244)

## Contract decision
Export has never been used in production. No legacy exporter migration, backward compatibility scaffolding, or version-preservation work is required. Keep one event cursor and make recovery evidence append-only. The operational transaction row may remain a mutable materialized projection; historical charger messages and recovery observations must remain durable.

## Verified producer observations
- `export_contract._event_rows` reads only `events`, by increasing `id`, and currently hardcodes `'ocpp' AS kind`. Internal recovery events therefore need an explicit origin/type representation and corresponding serializer support.
- `export_contract` selects transaction snapshots only for transaction IDs appearing in the current event page. An inferred stop without a new event cannot become visible after the consumer advances its cursor.
- The page's SQLite event read, transaction archive reads, and status reads are not a consistent cross-store snapshot.
- Existing PR #243 adds metadata and tests but does not itself implement recovery event persistence.

## Writer inventory requiring code-level verification in B
1. `EventStore.infer_transaction_stop`: SQLite state update and `transaction_recoveries` insert, no exportable event.
2. `TransactionArchive.infer_stop`: JSON rewrite of status/recovery.
3. `EventStore.record_transaction_stop`: state and recovery update, with separate inbound OCPP event recording.
4. `TransactionArchive.stop` and `_load_or_recover`: late stop, historical adoption and collision handling; unresolved evidence directory.
5. `EventStore.record_connector_status`: status-derived ending without explicit transaction correlation in the exported notification.
6. Disconnection timeout, retry/restart paths, and `runtime_events` (not part of exporter): locate every caller before editing.

## B implementation requirements
- Define internal recovery event envelope with event ID, transaction ID, charge point, previous/new state, reason, observed UTC time, and links to original event IDs where known. Never disguise an internal event as charger OCPP traffic.
- One event per accepted transition; duplicate/replayed no-ops append nothing.
- Commit SQLite state projection and event insertion together; test crash/restart consistency separately for JSON archives.
- Preserve CLI semantics, charging availability, original payloads, and energy units.
- Add deterministic tests for timeout, late StopTransaction, missing start, duplicate inference, restart gap, status ending, and ID collision.
- Ensure Reports can ingest/replay exclusively by existing event cursor.

## A verification boundaries
This audit is based on issue #244, the current exporter source, and PR #243 diff. It does **not** claim full recovery call-graph inspection, tests run, or CI green. Perform those checks before implementing B. No recovery production code changed in A.
