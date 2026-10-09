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
