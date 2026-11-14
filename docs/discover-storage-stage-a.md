# Discover active schema storage — issue #355, Stage A

## Scope and storage authority

Discover inherits `ocpp_csms_service_data_dir` from the Ansible CSMS
storage selection. It must use the **running CSMS writer's** versioned
directory, not the unversioned storage parent or a hardcoded `schemas/8`.
Before modifying Discover's stable command, persistent integration or
systemd unit, its role executes a read-only storage preflight. The installed
Discover service repeats this validation via `ExecStartPre` at each start.

The preflight inspects the live `ocpp-csms.service` MainPID and its single
`--data-dir`, checks readable SQLite evidence tables in `mode=ro`, and
rejects mismatches without initializing the database or modifying
Discover's adaptation. It does **not** continuously revalidate an already
running Discover process.

## Schema handoff and recovery

The product installer runs the CSMS role before the Discover role.
For an **effective storage-directory switch**, CSMS records whether
Discover was active and stops it **before** stopping the previous writer.
If Discover cannot be quiesced, CSMS refuses the writer handoff.

A failure **before the candidate writer may start** restores the previous
CSMS unit/service and then restarts Discover only if it was previously
active. The previous schema, SQLite database and Discover receipts must
remain unchanged. If the candidate writer **may have started**, CSMS
forbids automatic rollback, preserves its candidate storage/release and
leaves Discover stopped until a managed **fail-forward** reconvergence
installs the correct unit. An old Discover unit refuses a new start through
its `ExecStartPre`.

This is not an atomic two-service transaction. Failures in the combined
installer can occur after CSMS has changed storage but before the Discover
role converges. An operator must repair forward in that case, rather than
reactivating a historical database. For **same-schema release changes**,
the new quiescence step does not stop Discover unnecessarily.

## Validation and isolated CI evidence

Hermetic Python tests cover schema 8/9/12, missing/ambiguous MainPID,
`systemctl` and `/proc` failures, directory mismatch, missing SQLite
evidence, service preflight and read-only preservation. Existing Ansible
CI tests also verify no-op, staging, same-schema upgrades, schema
advancement, downgrade rejection, and pre/post-writer recovery.

The dedicated `discover_systemd` CI job uses a fresh **GitHub-hosted,
disposable** Ubuntu VM, refuses a pre-existing Discover service or an
`ocppci0` interface, then creates an **unaddressed Linux dummy
`ocppci0`** interface. It leaves the runner's actual `eth0` untouched
and binds CSMS to `127.0.0.1`. A guarded CI-only playbook calls the
**actual CSMS and Discover Ansible roles**. The production `install.yml`
and real interfaces are unchanged.

The fixture proves real systemd installation, initial activation,
`ExecStartPre`, restart, idempotent reconvergence and selected data-dir.
It injects a failure **inside cutover, after Discover quiescence but
before writer start**, and verifies actual restoration of **both**
services, the old schema, CSMS release/systemd unit, SQLite inode, integrity and preserved prior evidence
and Discover receipts. Separately, a failure **after the new writer
starts but before Discover convergence** verifies that Discover stays
stopped, the obsolete unit fails at `ExecStartPre`, and managed
reconvergence repairs forward to a synthetic next schema.

The fixture snapshots Discover-owned persistent files and canonical
nftables rule structures. It ignores volatile packet/byte counters and
handle identifiers of the disposable runner but rejects structural
changes; it also separately requires that no
`table ip ocpp_field_redirect` is installed during the test.
The only networking fixture is the disposable, unaddressed dummy device.

## Exclusions

Stage A CI does **not** certify actual EV Ready behavior, physical
chargers, real charger-facing interfaces, reboot of the complete
appliance, or field network connectivity. Routed HTTP remains
opt-in/default-off, and Stage B/C remain separate acceptance stages.
No changes to OCPP wire semantics, SQLite v8, source identities,
transaction history, production firewall rules, or production
deployments are implied by Stage A acceptance.

SQLite WAL checkpoints may change database file bytes during normal restart. Moreover, CSMS deliberately appends `server_started` and `server_stopped` audit rows to `runtime_events`. The pre-writer fixture requires the **same SQLite inode**, a successful read-only `integrity_check`, unchanged schema/version, **every prior row unchanged** and **every non-runtime-events table identical**. Only new, higher-ID startup/shutdown audit rows may appear; transaction, event, source identity and previously recorded runtime rows cannot change.
