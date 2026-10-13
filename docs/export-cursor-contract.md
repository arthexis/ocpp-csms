# Export cursor contract — issue #224, chunk A

This document records the existing `ocpp-csms/export/v1` behavior and the proposed additive extension. **Proposed fields and feeds are not implemented by this document.** Chunk B must implement and test them before consumers rely on them.

## Existing v1 semantics (verified against export_contract.py)

- `data.source_id` is persisted in SQLite `appliance_metadata` and identifies a CSMS data store, not a charger. Consumers must namespace all event and transaction identities by source ID. A restored/cloned database can retain the same source ID; operators must not run divergent clones as independent sources.
- `cursor.after` is an exclusive SQLite OCPP event ID lower bound; `cursor.next` is the last returned event ID, or `after` for an empty page. `cursor.more` means that at least one additional event was visible to the query when the page was read. IDs need not be contiguous.
- Pages are ordered by ascending event ID and contain at most `limit` events; `limit >= 1`, `after >= 0`. Runtime events are excluded. An empty page retains the supplied cursor.
- Retrying the same `after` is safe for event identity, but not a byte-identical snapshot guarantee: status and transaction archives can change between calls.
- `transactions` are the **current archive snapshots** for transaction IDs referenced by events on the page. They are read after the event query, not within a common database/archive snapshot. An archive-only correction may never appear in event pagination.
- `started_at`/`stopped_at` in exported transaction items currently prefer CSMS receive timestamps, falling back to archive timestamps. Do **not** interpret them as charger event time. `energy_wh` is watt-hours and is null when start/stop meter values are missing.
- No high-watermark, consistent snapshot, correction feed, gap detection or maximum page size is currently promised. An empty page is not evidence that transaction archives have not changed.

## Chunk B design decision

Use a **separate durable transaction-change feed**, not the event cursor. Persist a monotonic `change_id` for every observable transaction archive mutation (including corrections with no new event), with a stable transaction identity `(source_id, transaction_id)`, a canonical content fingerprint and UTC observation time. The write path must record the change durably and atomically with the authoritative mutation, or document and repair any crash window. Merely comparing `updated_at` or scanning transaction IDs referenced by new events is insufficient. Check all archive writers, recovery/reconciliation and import paths before choosing the persistence hook.

Expose a second exclusive cursor for changes and deterministic ascending ordering. Consumers upsert the latest revision by source and transaction ID, ignore duplicate `change_id` deliveries, and do not sum repeated snapshots. If a deletion is possible, emit a tombstone. Keep the event feed independent.

Add `exported_at` as timezone-aware UTC observation metadata, and an event high-watermark captured at a defined read boundary. **Do not call this a consistent snapshot**: SQLite event rows, filesystem transaction archives and status are not atomically read together. A bounded high-watermark is only an event boundary.

Preserve legacy transaction timestamps unchanged in v1. Add explicit nullable `start_event_at`, `start_received_at`, `stop_event_at`, `stop_received_at` (and meter sample event/receive provenance when available). Charger-reported timestamps must retain offset semantics or be normalized to UTC with documented conversion; invalid/missing timestamps become null, never silently replaced by receive time. Keep `energy_wh` nullable in Wh. Add explicit quality/completeness metadata with distinct active, incomplete, missing-meter, recovered/inferred and corrected indicators; never fabricate readings.

## Reset and compatibility rules

Persisted source identity plus cursors define replication state. If a source changes, consumers must initialize a new namespace, not continue the old cursor. If a cursor is ahead of the current maximum event ID, report a reset/gap condition rather than treating the empty page as successful completion. Internal event ID gaps may be legitimate (deletions or filtered rows); do not infer data loss from a numeric gap alone. A restored backup under the same source ID requires an explicit epoch/reset strategy before the feed can promise reset detection.

Only add fields to `export/v1` when existing field meaning is unchanged. If a field changes meaning, introduce `export/v2` and keep the existing forwarder functional during migration. No reporting, scheduling, SMTP or dependency on ocpp-reports belongs in CSMS.

## Chunk A acceptance tests

Contract tests must pin event ordering, exclusive `after`, inclusive `next`, empty-page behavior, replay identity, current non-atomic transaction snapshot semantics and nullable energy. Chunk B/C will extend these with correction-only changes, revision durability, reset detection, event/receive timestamp separation and consumer compatibility.
