# Architecture and persistence contracts

This document records the behaviors that operators and agents must understand before modifying persistent data or service deployment. Code and the currently running service remain authoritative for exact behavior.

## Source of truth

- SQLite is authoritative for persisted OCPP and runtime evidence. JSON archives and text views are diagnostics, not a second transaction ledger.
- Schema version is defined by `CURRENT_SCHEMA_VERSION` in `src/ocpp_csms/schema.py` (currently 8). Never infer the production schema solely from a README or a Git checkout.
- An observed OCPP command acknowledgement is not evidence that an intended physical action completed.
- CSMS maintains OCPP behavior and evidence; `ocpp-reports` owns downstream reporting, email delivery, and derived report persistence.

## Schema and deployment lifecycle

- The CSMS data root is separate from the selected service directory under `schemas/<version>`; a candidate schema directory on disk is not necessarily the active writer. Historical databases remain separate evidence sets.
- An application upgrade with an unchanged schema reuses its database. A schema advancement creates a new database instead of mutating an old schema in place.
- Reject downgrades before changing deployed state. Rollback of a newly introduced schema is only safe before new evidence is committed; after that, preserve the new evidence and investigate rather than blindly restoring older state.
- Transaction IDs may restart across schema databases: reports must use schema, transaction ID, and charger identity together rather than transaction ID alone.
- Deployment must be checked against the actual installed commit, unit and live writer, not just the latest GitHub SHA or a prepared schema directory. Re-running an unchanged commit is intended to be idempotent.
- For a schema change, Ansible quiesces active Discover before the writer handoff. A pre-writer failure can restore the prior service and Discover when safe; once a candidate writer might have started, automatic rollback is forbidden and recovery is **forward repair**, preserving evidence.

## Transaction and recovery semantics

- StartTransaction allocation and exact retries are persisted atomically; transaction history must remain interpretable across process restarts.
- Genuine StopTransaction evidence remains valid after a restart, including late messages. Do not synthesize stops or silently discard orphan evidence.
- Stored connector observations are evidence, not a guarantee of the current physical charger state.
- Historical data should be preserved for forensics and for ingestion by Reports. Destructive data resets are for explicitly verified test environments only.

## Runtime architecture

The WebSocket listener uses a direct event loop. Normal OCPP handling persists evidence after each handler completes; there is no Django, Celery worker, async queue, or desired-state engine behind the protocol path.

The listener uses plain `ws://` on `0.0.0.0:9000`. Configure network reachability deliberately; use nginx TLS termination for public `wss://` endpoints.

Operator control uses the active service `<data-dir>/control.sock`, a Unix-domain socket, rather than a second TCP control service.

Discover inherits the schema-selected `ocpp_csms_service_data_dir` as
`ocpp_discover_data_dir` from Ansible; it does not fall back to the
parent storage root. Before installing Discover integration and again on
Discover startup (`ExecStartPre`), a **read-only** storage contract
verifies the running CSMS `MainPID`, its unique `serve --data-dir`,
matching resolved directory, existing readable SQLite database, positive
schema version and required `events`, `discovery_sessions` and
`discovery_event_sessions` tables. Failure prevents new Discover startup
work, notifies the operator, and is not permission to discard durable
receipts or unrelated firewall state. An active service indicator alone
does not establish field connectivity or physical charger health.

## Data and evidence

Typical Ansible-managed storage layout (illustrative; inspect the live
service's `--data-dir` for the actual active version):

```text
~/ocpp-csms-data/                   # storage root, not the running database
  schemas/
    <version>/                     # service data directory selected by Ansible
      ocpp-csms.sqlite3            # SQLite evidence authority
      control.sock                 # operator Unix socket while service runs
      transactions/                # optional diagnostic JSON projections
    <historical-version>/          # preserved separate evidence
```

A prepared `schemas/<version>` candidate is not active merely because
it exists. The actual CSMS writer/installed unit establish the active
directory. Do not seek or create the running SQLite database directly under
the root to repair Discover; a mismatched service path must fail closed.

SQLite is authoritative for transaction identities, received OCPP evidence, transaction state, and reporting queries. Start retries retain their SQLite transaction identity while each received OCPP frame remains distinct evidence. Stop and meter evidence is retained even when its transaction is unknown; a later stop does not rewrite previously exported events. JSON transaction archives are optional diagnostics, not a fallback authority for transaction queries or exports. Export consumers advance using the SQLite-backed event cursor and may replay pages safely. In `ocpp-csms/export/v1`, `after` is an exclusive event ID, `next` is the last event ID returned (or `after` for an empty page), and `more` indicates whether additional events are available. Each page includes events and their normalized meter samples, snapshots of transactions touched by those events, and status read from one SQLite snapshot. Readers should persist the cursor only after successfully processing a page; repeating a page does not change its event identities.

Schema versions are independent from application releases. Startup does not silently upgrade an old database. Deployment performs explicit compatibility inspection and creates a SQLite-safe backup before a supported upgrade.

## Installed layout

Ansible uses immutable releases with stable commands:

```text
~/.local/share/ocpp-csms/releases/<revision>/
~/.local/share/ocpp-csms/current
~/.local/share/ocpp-csms/previous
/usr/local/bin/ocpp-csms
/usr/local/bin/ocpp-discover
```

Shared appliance state/integration:

```text
~/ocpp-csms-data/
/etc/systemd/system/ocpp-csms.service
/etc/systemd/system/ocpp-discover.service
/etc/ocpp-discover/nftables.conf
/var/lib/ocpp-discover/discovered.json
/run/ocpp-discover/
```
