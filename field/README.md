# Field harness

This directory contains real-hardware validation tooling that remains useful outside the production appliance runtime. Production discovery, redirect, handoff, persistence, and Smart Charging operations belong to `ocpp_discover` and `ocpp_csms`; they are no longer mirrored here.

The field harness is intentionally separate from both production packages and from their normal regression suites.

Run its self-tests explicitly:

```sh
python -m pytest field/tests
```

## Purpose

The harness exists for controlled charger-facing validation that spans service replacement and real hardware behavior. It can:

- capture a pre-takeover safety baseline;
- switch from a configured legacy service to a candidate CSMS;
- require reconnect, inbound Heartbeat evidence, and idle state;
- exercise Soft/Hard reset and GetConfiguration behavior;
- enter an unattended idle soak guarded by an independent watchdog;
- roll back to the configured legacy service when strong local candidate failures are detected;
- archive evidence across repeated attempts.

It does not install software, discover charger network endpoints, manage nftables, or replace appliance deployment logic.

## Configuration and preflight

All environment-specific values are supplied by the caller. The harness does not assume service names, listener ports, executable locations, data directories, or control-socket paths.

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

`--idle-confirmed` is an explicit operator assertion about the pre-takeover charger state. A successful preflight records the run configuration and evidence. Reusing a run directory with different operational configuration is rejected.

## Takeover and rollback

After preflight:

```sh
python -m field.harness takeover /path/to/run
```

The takeover requires the configured legacy service, listener release, candidate service/listener/control socket, charger reconnect, fresh inbound Heartbeat evidence, and no active transaction. Evidence is read directly from the configured CSMS store rather than from human-readable CLI text.

Any takeover failure after switching begins uses the common rollback primitive. Rollback can also be requested explicitly:

```sh
python -m field.harness rollback /path/to/run --reason operator_requested
```

Rollback is idempotent, disarms the watchdog, and touches only the configured services and listener state.

## Retesting an updated candidate

The harness does not update source code or packages. Deploy a new candidate using the host's normal deployment mechanism, then refresh the existing run:

```sh
python -m field.watchdog disable /path/to/run
python -m field.harness refresh /path/to/run
```

`refresh` requires the candidate service, listener, and control socket to be healthy, the legacy service to remain inactive, the charger to be connected and idle, and fresh Heartbeat evidence to exist. Previous phase evidence is archived under numbered `attempts/` directories before the run returns to the baseline phase.

## Reboot and configuration protocol

From baseline:

```sh
python -m field.harness reboot-config /path/to/run
```

The protocol:

1. checkpoints current evidence;
2. sends one Soft reset and requires `Accepted`;
3. waits for a charger disconnect;
4. sends one Hard reset only if the Soft reset does not produce a disconnect within the configured interval;
5. requires post-checkpoint disconnect, reconnect, `BootNotification`, and inbound `Heartbeat`;
6. queries all configuration keys;
7. queries a selected key set and repeats that selected query after a delay;
8. records semantic differences without turning charger-reported value changes into automatic failures;
9. requires the charger to remain connected and idle.

Example:

```sh
python -m field.harness reboot-config /path/to/run \
  --key HeartbeatInterval \
  --key SupportedFeatureProfiles \
  --reboot-timeout 60 \
  --post-boot-timeout 120 \
  --repeat-delay 30
```

The field harness does not perform configuration writes.

## Watchdog

The watchdog is independent from both the harness controller and `ocpp-csms`:

```sh
python -m field.watchdog enable /path/to/run
python -m field.watchdog status /path/to/run
python -m field.watchdog run /path/to/run --interval 30 --failure-threshold 3
python -m field.watchdog disable /path/to/run
```

Only strong local candidate failures contribute to automatic rollback:

- candidate service inactive;
- listener unavailable;
- control socket unavailable.

Charger disconnects, missing Heartbeats, and charger evidence-read errors are recorded as diagnostics but do not independently trigger rollback because they may reflect site power, charger, or network conditions.

## Idle soak and handoff

After successful reboot/configuration validation:

```sh
python -m field.harness soak /path/to/run
```

`soak` verifies the candidate is healthy, the legacy service is inactive, the charger is connected and idle, and then arms watchdog state. The watchdog process itself must still be supervised separately.

When the field run is complete:

```sh
python -m field.harness handoff /path/to/run
```

`handoff` verifies the candidate remains healthy, disables the watchdog, records final evidence, and leaves service state unchanged.

## Active field modules

```text
field/
  harness.py     controlled real-hardware protocol
  watchdog.py    independent health guard and rollback trigger
  protocol.py    control-socket protocol helpers
  evidence.py    evidence inspection helpers
  state.py       run-state persistence
  system.py      service/listener primitives
  tests/         harness self-tests
```

Network discovery and adaptation now live exclusively under `src/ocpp_discover/`. Smart Charging diagnostics use the installed `ocpp-csms profile ...` command surface.