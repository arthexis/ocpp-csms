# Automatic recovery of disconnected transactions

The CSMS service runs a small recovery scan every **5 minutes**, with an
inactivity threshold of **60 minutes**. This requires persisted
`charger_disconnected` evidence for the charge point, and no in-process
connected session. The timeout begins no earlier than the latest disconnect or
transaction activity, including timestamps received by the server. An active
connection or recent activity prevents inference even if a transaction is old.

Eligible transactions transition to `inferred_stopped` through the existing
JSON archive and SQLite audit primitives. This is **administrative inference**:
it does not prove that the physical charger has stopped supplying power. No
`StopTransaction`, final meter value or energy amount is fabricated. Late
authoritative `StopTransaction` messages can reconcile the inferred record.

If the archive update succeeds but SQLite update fails, the following scan
retries the SQLite side without discarding the archived recovery evidence.
An interrupted service restart does not reset inactivity timestamps, since the
evidence comes from existing persisted runtime and transaction records. Repeated
scans are idempotent.

There is no new Celery queue, timer unit, deployment dependency, or standalone
daemon. The loop is owned and cancelled by the running CSMS service.

Manual `transactions recover` commands, configurable timeout settings, and
deployment preflight integration are planned for Chunk 3. Deployment preflight
continues to reject truly open transactions until the recovery check succeeds.


## Manual inspection and recovery (Chunk 3)

Use the CSMS CLI with the correct `--data-dir` (the flag is before the command):

```sh
ocpp-csms --data-dir ~/ocpp-csms-data transactions recover --dry-run
ocpp-csms --data-dir ~/ocpp-csms-data transactions recover --cp SIM001 --dry-run
```

The preview never modifies transactions. To **perform** offline recovery,
stop the CSMS service first, ensure the charger is disconnected, and run:

```sh
ocpp-csms --data-dir ~/ocpp-csms-data transactions recover --cp SIM001
```

This uses exactly the same disconnect/inactivity eligibility policy as the
automatic worker; it does not force close a connected or recent session.
Writing through the CLI is refused while the CSMS process is active, avoiding
an unsafe race with the live service. The database must already be at the
current schema version. Upgrade it explicitly using the existing schema
upgrade workflow before attempting recovery.

## Configure local policy

```sh
ocpp-csms --data-dir ~/ocpp-csms-data recover --policy
ocpp-csms --data-dir ~/ocpp-csms-data recover --policy --timeout-minutes 60 --interval-minutes 5
ocpp-csms --data-dir ~/ocpp-csms-data recover --policy --disable
ocpp-csms --data-dir ~/ocpp-csms-data recover --policy --enable
```

Policy is atomically saved under `recovery-policy.json` in the data directory,
and the service reloads it at each scan. This is server-local configuration,
not OCPP ChangeConfiguration on a charger.

Deployment preflight **does not automatically close transactions**. It continues
to refuse potentially active transactions and prints guidance for explicit
preview/recovery. An inferred stop is an administrative classification only;
it must not be used as proof that a real charger stopped delivering energy.
