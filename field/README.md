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

Rollback is idempotent and uses only the configured services and listener values.

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

Evidence written by this phase includes `reboot.json`, `config/all.json`, `config/selected.json`, `config/repeat.json`, and `configuration.json`. Unknown configuration keys are evidence, not failure. This protocol never sends `force=true` and never performs configuration writes.

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

Inspect stored harness state at any point with:

```sh
python -m field.harness status /path/to/run
```

The soak transition and explicit Monday handoff remain for chunk 5.
