# CSMS export cursor contract (#224)

## Existing contract: ocpp-csms/export/v1

The CSMS export command owns all reads of operational SQLite and transaction archives. OCPP Reports consumes the command's JSON output; it must not open CSMS files or write to CSMS databases.

The persisted `source_id` identifies the CSMS data store. `cursor.after` is the exclusive lower bound on OCPP event IDs; `cursor.next` is the last returned ID (or `after` for an empty page); `cursor.more` indicates another visible event. Events are ascending and limited by `limit`. Event IDs need not be contiguous. `after >= 0`, `limit >= 1`. A repeated request may return the same event identities, but transaction snapshots are not immutable across requests.

`events` contains OCPP frames from the operational event table, and `energy` contains samples derived from the page's MeterValues. `transactions` contains current archive snapshots for transaction IDs referenced by the event page. The exporter also returns `status`. These reads are not an atomic cross-store snapshot. The export is read-only and does not change the operational SQLite journal mode.

## Additive metadata (chunk B)

Transaction snapshots additionally expose nullable `start_event_at`, `start_received_at`, `stop_event_at`, `stop_received_at`, `meter_sample_times`, and a `quality` object with `active`, `incomplete`, `missing_meter`, `recovered`, `inferred`, and `corrected` flags. Timestamp provenance is normalized to UTC only for valid timezone-aware timestamps; unknown, invalid or timezone-naive timestamps remain null. Existing `started_at` and `stopped_at` fields keep their meanings; energy remains in Wh with unknown values represented as null.

The `corrected` flag identifies an inferred recovery reconciled by a real stop; it is not a generic manual-edit detector. A delayed StopTransaction produces a new event ID and causes the exporter to include the transaction's current snapshot in the next event page.

## Recovery gap and separate follow-up

Internal inferred-stop and some other recovery transitions currently mutate derived state without inserting an event into the exportable `events` stream. They are not guaranteed to be observable by the existing cursor. Track the append-only event representation, transaction correlation, idempotency, and SQLite/JSON crash consistency in #244. Do not invent charger OCPP frames or claim that current export captures these transitions until #244 is implemented.

No second SQLite database, independent transaction revision feed, forwarder compatibility code, or reporting-specific database mode is required.

## Cursor caveats

A changed `source_id` is a new namespace. A copied or restored database may retain the same source ID, so same-source backup rollback cannot reliably be detected by `source_id` alone. A cursor beyond the highest event ID currently yields an empty page; explicit reset/high-watermark behavior is a separate design concern. Consumers advance cursors only after durable ingestion and deduplicate by `(source_id, event_id)`.
