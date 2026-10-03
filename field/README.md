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

A successful preflight records:

- `state.json`: authoritative field-run configuration and phase;
- `preflight.json`: individual preflight decisions.

Reusing a run directory with different operational configuration is rejected.

## Chunk 2: takeover, baseline, and rollback

After successful preflight, switch from the configured legacy service to the configured candidate CSMS with:

```sh
python -m field.harness takeover /path/to/run
```

The takeover requires, in order:

1. the configured legacy service is active;
2. it stops and releases the configured listener;
3. the configured CSMS service starts;
4. that service is active, the configured listener is reachable, and the configured control socket exists;
5. the configured charger is recorded as connected;
6. at least one inbound `Heartbeat` is present in the configured CSMS data directory;
7. no active transaction is recorded for that charger.

The baseline evidence is read directly and read-only from the candidate CSMS SQLite evidence store. Human-readable CLI output is not parsed.

Timeouts are caller-configurable:

```sh
python -m field.harness takeover /path/to/run \
  --service-timeout 30 \
  --baseline-timeout 90 \
  --poll-interval 1
```

Any takeover failure after service switching uses the common rollback primitive. Rollback can also be requested explicitly:

```sh
python -m field.harness rollback /path/to/run --reason operator_requested
```

Rollback is idempotent. It stops the configured candidate service if needed, waits for the configured listener to become free, starts the configured legacy service, verifies that service and listener, and preserves the field-run evidence. Results are written to `rollback.json`.

Successful takeover records `takeover.json` and `baseline.json` and leaves the run in phase `baseline`.

Inspect stored state at any point with:

```sh
python -m field.harness status /path/to/run
```

Reboot and `GetConfiguration` protocol behavior remain for chunk 3.
