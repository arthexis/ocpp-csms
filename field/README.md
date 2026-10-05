# Field harness

This directory contains real-hardware field-test tooling. It is deliberately separate from both the production `ocpp_csms` package and the normal CSMS regression tests.

Normal regression tests remain under `tests/` and run with:

```sh
python -m pytest
```

Field-harness self-tests live under `field/tests/` and run explicitly with:

```sh
python -m pytest field/tests
```

## Passive Ethernet discovery

`field.discover` starts with a passive ARP observation on `eth0` by default. It requires repeated unanswered requests from one requester before producing a discovery candidate and refuses ambiguity.

```sh
python -m field.discover
python -m field.discover --interface eno1
```

The address-claim primitive used by later discovery stages is deliberately additive: after root authorization it can add only the discovered target as a `/32` secondary IPv4 address and records that exact ownership in `address.json`. Cleanup removes only the address named in that receipt and never replaces or flushes pre-existing interface addresses.

For a complete transactional discovery attempt, run:

```sh
sudo python -m field.discover run \
  --data-dir /path/to/csms-data \
  --state-dir /run/ocpp-discover \
  --interface eth0 \
  --listen-port 9000
```

`run` first gives an already-configured charger a grace period to connect to the normal CSMS. If one appears, it exits without invoking packet capture, `ip`, or `nft`. Otherwise it performs passive ARP discovery, claims the unresolved target as an additive `/32`, observes plaintext WebSocket traffic only from that charger, writes `redirect.json`, applies the narrow redirect, and waits for the charger to appear in the CSMS status store. Only that real CSMS session marks discovery successful.

Successful discovery preserves `address.json`, `redirect.json`, and `discovery.json` so the discovered network identity remains available for reconnects. A failed attempt rolls back any address or redirect created by that attempt. Existing discovery receipts or an unexpected pre-existing `ocpp_field_redirect` table are refused rather than layered over.

Explicit cleanup removes only discovery-owned state:

```sh
sudo python -m field.discover cleanup --state-dir /run/ocpp-discover
```

For normal appliance use, the repository-root `discover.sh` wrapper provides the supported surface: running it directly starts discovery now, `--install` installs Debian dependencies and enables `ocpp-discover.service` at boot, `--uninstall` disables/removes the service and cleans discovery-owned state, and `--cleanup` removes only the current network adaptation. The base `install.sh` exposes the same optional feature as `--with-discover` and `--without-discover`; omitting either flag preserves the existing discovery state.

## Production existing-endpoint handoff

For chargers that already reach a host-owned plaintext OCPP endpoint, `field.handoff` supports a two-stage Path A handoff that learns the endpoint before the old listener is removed.

First, while the old charger-facing listener is still available, capture and persist validated endpoint evidence:

```sh
sudo python -m field.handoff prepare \
  --state-dir /path/to/handoff-run \
  --interface eth0 \
  --listen-port 9000 \
  --seconds 30
```

A successful prepare writes immutable `handoff-endpoint.json`. It records the charger source, destination IP/port, WebSocket Host/path, capture interface, and target local CSMS listener. Prepare performs no service stop/start, address claim, nftables mutation, or Discover-state mutation, and it refuses to overwrite existing handoff evidence.

After confirming the target CSMS listener is already available, execute the controlled cutover:

```sh
sudo python -m field.handoff cutover \
  --data-dir /path/to/csms-data \
  --state-dir /path/to/handoff-run \
  --old-service OLD.service \
  --timeout 30
```

The cutover runs a read-only service-handoff preflight, baselines the currently connected idle charger set plus connection and inbound-OCPP evidence, and repeats the active-charge check immediately before disruption. It then stops the old listener, materializes the narrow redirect from the previously validated receipt, and requires both a fresh `charger_connected` event and fresh inbound OCPP traffic before success.

If anything fails after the old listener has stopped—including redirect application, charger reconnect, or fresh OCPP evidence—the handoff removes only the redirect owned by that attempt, restarts the old service, verifies it is active again, and preserves `handoff-endpoint.json` for diagnosis or a later retry. If rollback itself fails, the reported error includes both the original handoff failure and rollback failure.

This handoff always blocks active charging and does not use ARP fallback or address claiming.

## Plaintext OCPP redirect helper

Issue #53 uses a field-only helper for discovering and temporarily redirecting one observed plaintext OCPP WebSocket flow. It does not configure the host's gateway, DHCP, routing, NetworkManager, or persistent firewall state.

First passively capture a bounded observation window while the intended local CSMS listener is already available:

```sh
python -m field.redirect capture \
  --interface eth0 \
  --listen-port 9000 \
  --seconds 30 \
  --run-dir /path/to/redirect-run
```

A successful capture writes `redirect.json`. The receipt records one charger source, the observed destination IPv4 addresses, HTTP Host/path evidence, interface, local listener port, and capture time. Ambiguous sources, TLS/opaque traffic, non-WebSocket HTTP, unavailable listeners, and receipt overwrite are rejected.

Before any mutation, render and syntax-check the exact nftables candidate:

```sh
python -m field.redirect validate /path/to/redirect-run
```

Validation rechecks the receipt and requires the destination set to match the captured WebSocket request evidence exactly. It renders one dedicated `table ip ocpp_field_redirect`, scoped to the captured input interface, charger source IPv4, observed destination IPv4 set, observed destination TCP port, and local listener port, then checks it with `nft -c`.

Applying and removing the temporary redirect are explicit root-only operations:

```sh
sudo python -m field.redirect apply /path/to/redirect-run
sudo python -m field.redirect remove /path/to/redirect-run
```

`apply` revalidates the receipt, rechecks that the local listener is available, refuses to proceed if the dedicated table already exists, runs `nft -c` again, and then loads exactly the checked ruleset. `remove` deletes only `table ip ocpp_field_redirect` and refuses if that table is absent. Neither command writes a systemd unit or persistent nftables configuration.

The replacement field host must already provide the charger-facing gateway role that makes the original flow visible. Gateway/DHCP/NAT provisioning remains deployment responsibility and is intentionally outside this helper.

## Configuration

All environment-specific operational values are supplied by the caller. The harness does not assume service names, ports, listener addresses, executable locations, CSMS data directories, or control-socket paths.

Create a run and perform read-only preflight:

```sh
python -m field.harness start \
  --run-dir /path/to/run \
  --charger CHARGER_ID \
  --legacy-service LEGACY.service \
  --csms-service CANDIDATE.service \
  --ocpp-command /path/to/ocpp-csms \
  --listener-host 0.0.0.0 \
  --listener-port PORT \
  --csms-data-dir /path/to/data \
  --control-socket /path/to/control.sock \
  --idle-confirmed
```

Both service names must identify existing service units. How those units are installed and configured is intentionally outside the harness; this lets a field host use its own service definitions without embedding machine-specific launch paths in the test code.

`--idle-confirmed` is an explicit operator assertion for the pre-takeover state. The harness does not infer transaction state from a legacy backend with an unrelated storage model.

A successful preflight records `state.json` and `preflight.json`. Reusing a run directory with different operational configuration is rejected.

## Takeover, baseline, and rollback

After successful preflight, switch from the configured legacy service to the configured candidate CSMS with:

```sh
python -m field.harness takeover /path/to/run
```

The takeover requires the configured legacy service, listener release, configured CSMS service/listener/control socket, charger connection, at least one inbound `Heartbeat`, and no active transaction. Evidence is read directly and read-only from the configured CSMS SQLite store; human-readable CLI output is not parsed.

Timeouts are caller-configurable:

```sh
python -m field.harness takeover /path/to/run \
  --service-timeout 30 \
  --baseline-timeout 90 \
  --poll-interval 1
```

Any takeover failure after switching begins uses the common rollback primitive. Rollback can also be requested explicitly:

```sh
python -m field.harness rollback /path/to/run --reason operator_requested
```

Rollback is idempotent, disarms the watchdog, and uses only the configured services and listener values.

## Updating an already-running candidate and retesting

The harness intentionally does **not** update source code, install packages, or restart services. Those remain operator/agent deployment steps. This keeps field safety policy separate from deployment mechanics.

When the configured candidate CSMS is already serving the charger and a newer repo/package version needs to be validated, use this sequence:

1. confirm the charger is idle;
2. disable the field watchdog before deliberately interrupting or restarting the candidate:

   ```sh
   python -m field.watchdog disable /path/to/run
   ```

3. update the repository/package by the host's normal deployment method;
4. restart the configured candidate CSMS service and leave the configured legacy service stopped;
5. wait for the candidate listener and control socket to return;
6. re-baseline the existing field run:

   ```sh
   python -m field.harness refresh /path/to/run
   ```

`refresh` does not stop or start either configured service. It requires the candidate service, listener, and control socket to be healthy; requires legacy to remain inactive; requires the charger to be connected, have Heartbeat evidence, and have no active transaction; then moves the run back to phase `baseline` so `reboot-config` performs a real new test.

Before replacing current phase evidence, `refresh` archives the previous attempt under a numbered directory such as:

```text
attempts/001/
  baseline.json
  reboot.json
  configuration.json
  config/
  soak.json
  handoff.json
  result.json
```

Only files that exist are archived. The SQLite event store is not copied or rewritten; it remains the continuous raw evidence source. Subsequent refreshes use `attempts/002/`, `attempts/003/`, and so on.

After refresh, rerun the protocol normally:

```sh
python -m field.harness reboot-config /path/to/run
python -m field.harness soak /path/to/run
```

Entering `soak` arms watchdog state again; the watchdog supervisor process must still be running or be launched by the operator as described below.

## Reboot and GetConfiguration

After the run reaches `baseline`, execute:

```sh
python -m field.harness reboot-config /path/to/run
```

The reboot protocol checkpoints the candidate CSMS evidence store immediately before issuing `Reset`. Historical disconnects, `BootNotification`, or Heartbeats from before that checkpoint cannot satisfy the protocol.

The sequence is:

1. send one Soft reset through the configured control socket;
2. require an Accepted confirmation;
3. wait for a charger disconnect for the configurable reboot interval;
4. if no disconnect occurs, send exactly one Hard reset and require Accepted;
5. require post-checkpoint disconnect, reconnect, `BootNotification`, and inbound `Heartbeat`;
6. query all configuration keys;
7. query a selected key set;
8. repeat the selected query after a configurable delay;
9. record semantic differences without treating a changed charger-reported value as an automatic protocol failure;
10. require the charger to remain connected and idle.

Default selected keys are `SupportedFeatureProfiles`, `GetConfigurationMaxKeys`, `HeartbeatInterval`, and `MeterValueSampleInterval`. Override the selected set by repeating `--key`.

```sh
python -m field.harness reboot-config /path/to/run \
  --key HeartbeatInterval \
  --key SupportedFeatureProfiles \
  --reboot-timeout 60 \
  --post-boot-timeout 120 \
  --repeat-delay 30 \
  --poll-interval 1
```

The control socket location comes from `state.json`; the harness does not assume that it is `<data-dir>/control.sock`.

Evidence written by this phase includes `reboot.json`, `config/all.json`, `config/selected.json`, `config/repeat.json`, and `configuration.json`. Unknown configuration keys are evidence, not failure. Shareable configuration JSON preserves every key and metadata while replacing only sensitive values with `[REDACTED]`; the raw OCPP evidence remains in SQLite. This protocol never sends `force=true` and never performs configuration writes.

Successful completion leaves the run in phase `configuration`.

## Independent unattended watchdog

The watchdog is a separate process from both the field harness controller and `ocpp-csms`. It uses the run configuration already stored in `state.json` and has an explicit enabled/disabled state:

```sh
python -m field.watchdog enable /path/to/run
python -m field.watchdog status /path/to/run
python -m field.watchdog disable /path/to/run
```

`enable` and `disable` only change watchdog state. They do not start, stop, enable, or disable either configured CSMS service. A supervisor may run the independent watchdog process with:

```sh
python -m field.watchdog run /path/to/run \
  --interval 30 \
  --failure-threshold 3 \
  --rollback-timeout 30 \
  --rollback-poll-interval 1
```

The watchdog remains active only while stored watchdog state is `enabled`. Disabling it causes the next loop iteration to exit without changing either CSMS service.

Each check records the candidate CSMS service state, configured listener, configured control socket, charger connection state, last Heartbeat evidence, and active transaction evidence. Snapshots are appended to `watchdog-snapshots.jsonl`; the newest snapshot is also stored in `watchdog-latest.json`.

Only strong local CSMS failures contribute to automatic rollback:

- configured candidate CSMS service is inactive;
- configured listener is unavailable;
- configured control socket is unavailable.

These failures are debounced by `--failure-threshold`; the counter resets after a healthy check. When the threshold is reached, the watchdog invokes the same common rollback primitive used by the harness and then disarms itself.

Charger disconnects, delayed/missing Heartbeats, reconnect behavior, and charger evidence-read errors are recorded as diagnostics but do **not** trigger automatic rollback. They may reflect charger, site power, or network conditions rather than a local CSMS failure.

For supervision or diagnostics, `--once` performs one health check and exits:

```sh
python -m field.watchdog run /path/to/run --once
```

The watchdog contains no hardcoded field service names, paths, hosts, ports, or socket locations.

## Idle soak and Monday handoff

After successful configuration validation, enter the unattended soak with:

```sh
python -m field.harness soak /path/to/run
```

`soak` re-verifies that the configured candidate CSMS is active, the configured legacy service is inactive, and the configured listener and control socket are available; requires the charger to be connected, have Heartbeat evidence, and have no active transaction. Only after those checks pass does the harness set phase `idle_soak` and arm the watchdog. The command itself does not start a watchdog supervisor; the separate `field.watchdog run` process must already be supervised or launched by the operator.

The soak remains protected until either the watchdog rolls back or an operator deliberately hands control to the next field protocol. There is no time-based automatic expiry.

For the Monday transition to issue #45, use:

```sh
python -m field.harness handoff /path/to/run
```

`handoff` is intentionally non-mutating with respect to the configured CSMS services. It verifies that the candidate CSMS is still active, legacy remains inactive, and the configured listener/control socket are available; then it disables the watchdog and records `handoff.json`. It does **not** stop the candidate CSMS or start legacy. Successful handoff leaves the run in phase `handed_off`, ready for production-style startup configuration and active-charge testing under #45.
