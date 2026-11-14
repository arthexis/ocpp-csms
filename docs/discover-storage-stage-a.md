# Discover active storage — issue #355, Stage A

## Contract

Discover uses the effective `ocpp_csms_service_data_dir` selected by CSMS
storage planning and cutover. No hardcoded schema version or unversioned
fallback database is permitted. Before the Discover role changes its stable
command, persistent integration, nftables boot registration or systemd unit,
the read-only `ocpp_discover.storage_contract` checks the active CSMS MainPID,
its `--data-dir`, SQLite database and evidence tables. The same check runs
at service start through `ExecStartPre`. Mismatches fail closed.

## Failure and recovery boundary

The combined installer runs CSMS before Discover. Its Discover preflight is
not a preflight of CSMS mutation: APT and a CSMS schema handoff may already
have happened. After the replacement CSMS writer might have started,
automatic rollback is forbidden by the pre-existing cutover contract.
Repair must be **fail-forward** against the active schema; do not reactivate
historical transactions or an old database. A preflight failure leaves
Discover's durable adaptation and firewall configuration untouched by
the Discover role. On later service starts, an old unit whose data-dir no
longer matches the writer fails `ExecStartPre` before any new discovery.
An already-running Discover process is not continuously revalidated;
cross-service atomic activation and reboot acceptance remain unproven.

## Test evidence and limitations

Hermetic tests cover schema 8/9/12, missing or mismatched SQLite evidence,
missing/inaccessible MainPID, nonnumeric systemctl response, command errors,
read-only preservation, and selected-schema switch/reversal. Disposable CI
Ansible tests deploy CSMS and run a separate read-only integration probe
after the candidate deploy and after idempotent/stage-only reconvergence.
The probe reads the real installed CSMS MainPID, renders the actual Discover
unit template with that selected path, executes its `ExecStartPre` checker,
rejects the incorrect parent directory, and verifies no persistent
Discover state was modified.

**CI limitation:** GitHub-hosted Ansible runners currently lack `eth0`;
`install.yml` skips the Discover role. Green CI therefore verifies the
rendered unit and live CSMS storage but does **not** prove actual Discover
service installation, startup, restart, reboot or physical EV Ready contact.
When `eth0` exists on a disposable test runner, the probe also verifies
the installed unit and its active state. Full field acceptance is separate.

Stage B/C, routed HTTP opt-in (default off), nftables rules, OCPP wire
semantics, SQLite v8, source identity and customer transactions are unchanged.


## Real disposable systemd/Ansible Stage A gate

The CI-only \`discover_systemd\` job runs on a fresh GitHub-hosted VM.
Its fixture refuses existing eth0 or installed Discover, creates a Linux
dummy eth0 with no address or peer, and installs the **actual product roles**
with systemd. It checks successful first start, idempotent redeploy, real
restart, persistent receipt/configuration preservation and unchanged **live**
nftables ruleset. The runner is discarded after CI; no field appliance,
real network connection, or physical charger is accessed.

A test-only playbook executes the CSMS role on a temporary schema+1
commit, deliberately fails after its real writer handoff, and does **not**
converge the Discover role. Stage A quiesces a previously active Discover
before the old schema writer stops; the test checks that the old Discover
remains stopped and its stale systemd unit fails ExecStartPre. A subsequent
normal managed candidate deploy repairs forward and validates the updated
Discover unit and restart. Failed pre-writer CSMS handoffs restore the
previously running Discover only after the old CSMS service is restored.
The writer-started no-rollback guard remains unchanged.

This CI gate does not certify real EV Ready traffic, connectivity across
a reboot, routed HTTP opt-in, or any field integration.
