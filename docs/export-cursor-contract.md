# CSMS export cursor contract (#224)

## Existing contract: ocpp-csms/export/v1

The CSMS export command owns all reads of authoritative operational SQLite evidence. JSON transaction archives are optional diagnostics, not an export source. OCPP Reports consumes the command's JSON output; it must not open CSMS files or write to CSMS databases.

The persisted `source_id` identifies the CSMS data store. `cursor.after` is the exclusive lower bound on OCPP event IDs; `cursor.next` is the last returned ID (or `after` for an empty page); `cursor.more` indicates another visible event. Events are ascending and limited by `limit`. Event IDs need not be contiguous. `after >= 0`, `limit >= 1`. A repeated request may return the same event identities, but transaction snapshots are not immutable across requests.

`events` contains OCPP frames from the operational event table, and `energy` contains samples derived from the page's MeterValues. `transactions` contains SQLite-derived snapshots for transaction IDs referenced by the event page. SQLite-derived `status` fields, events, and transaction snapshots are read from the same SQLite read transaction. Non-database status inputs (such as process liveness and RFID configuration) are sampled independently and are not transactionally synchronized with SQLite. The export is read-only and does not change the operational SQLite journal mode.

## Additive metadata (chunk B)

Transaction snapshots additionally expose nullable `start_event_at`, `start_received_at`, `stop_event_at`, `stop_received_at`, `meter_sample_times`, and a `quality` object with `active`, `incomplete`, `missing_meter`, `recovered`, `inferred`, and `corrected` flags. Timestamp provenance is normalized to UTC only for valid timezone-aware timestamps; unknown, invalid or timezone-naive timestamps remain null. Existing `started_at` and `stopped_at` fields keep their meanings; energy remains in Wh with unknown values represented as null.

A delayed StopTransaction produces a new event ID; export must expose the resulting transaction state through the event cursor. Legacy recovery-related quality fields do not imply a synthetic stop or mutable recovery state.

## Cursor caveats

A changed `source_id` is a new namespace. A copied or restored database may retain the same source ID, so same-source backup rollback cannot reliably be detected by `source_id` alone. A cursor beyond the highest event ID currently yields an empty page; explicit reset/high-watermark behavior is a separate design concern. Consumers advance cursors only after durable ingestion and deduplicate by `(source_id, event_id)`.

## Operator-triggered historical SoC metadata (issue #124)

`ocpp-csms transaction-soc --cp T175-IT1-0620-055 --transaction 247`
returns a one-transaction, read-only `ocpp-csms/transaction-soc/v1`
record: persisted `source_id`, exact charger and transaction identity,
nullable `soc_start` / `soc_end`, and `ocpp-meter-values` provenance.
The producer derives these fields exclusively from authoritative OCPP SoC
samples; if there is only one measurement, starting SoC remains null.
No RFID, customer history, raw meter frames, events, or mutable cursor
are included. Wrong source/transaction identities fail closed.

This is intentionally independent of the `export/v1` event cursor.
Consumers must still verify source identity and a matching preexisting
transaction projection before updating their own derived metadata.
The producer never writes operational SQLite or changes charging state.

