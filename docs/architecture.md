# Architecture and persistence contracts

This document records the behaviors that operators and agents must understand before modifying persistent data or service deployment. Code and the currently running service remain authoritative for exact behavior.

## Source of truth

- SQLite is authoritative for persisted OCPP and runtime evidence. JSON archives and text views are diagnostics, not a second transaction ledger.
- Schema version is defined by `CURRENT_SCHEMA_VERSION` in `src/ocpp_csms/schema.py` (currently 8). Never infer the production schema solely from a README or a Git checkout.
- An observed OCPP command acknowledgement is not evidence that an intended physical action completed.
- CSMS maintains OCPP behavior and evidence; `ocpp-reports` owns downstream reporting, email delivery, and derived report persistence.

## Schema and deployment lifecycle

- Installed processes use a schema-version-specific database under `schemas/<version>`; historical databases remain available as separate evidence sets.
- An application upgrade with an unchanged schema reuses its database. A schema advancement creates a new database instead of mutating an old schema in place.
- Reject downgrades before changing deployed state. Rollback of a newly introduced schema is only safe before new evidence is committed; after that, preserve the new evidence and investigate rather than blindly restoring older state.
- Transaction IDs may restart across schema databases: reports must use schema, transaction ID, and charger identity together rather than transaction ID alone.
- Deployment must be checked against the actual installed commit and service configuration, not just the latest GitHub SHA. Re-running an unchanged commit is intended to be idempotent.

## Transaction and recovery semantics

- StartTransaction allocation and exact retries are persisted atomically; transaction history must remain interpretable across process restarts.
- Genuine StopTransaction evidence remains valid after a restart, including late messages. Do not synthesize stops or silently discard orphan evidence.
- Stored connector observations are evidence, not a guarantee of the current physical charger state.
- Historical data should be preserved for forensics and for ingestion by Reports. Destructive data resets are for explicitly verified test environments only.

## Runtime architecture

The WebSocket listener uses a direct event loop. Normal OCPP handling persists evidence after each handler completes; there is no Django, Celery worker, async queue, or desired-state engine behind the protocol path.

The listener uses plain `ws://` on `0.0.0.0:9000`. Configure network reachability deliberately; use nginx TLS termination for public `wss://` endpoints.

Operator control uses `<data-dir>/control.sock`, a Unix-domain socket, rather than a second TCP control service.

## Data and evidence

Default CSMS data:

```text
~/ocpp-csms-data/
  control.sock
  ocpp-csms.sqlite3
  ocpp-csms.sqlite3.schema-<old-version>.bak
  transactions/
```

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
