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

## Chunk 1: state and preflight

The current harness is read-only with respect to services. It creates or resumes a run directory, records explicit operational configuration, and performs non-mutating preflight checks. It cannot start, stop, restart, enable, or disable any service yet.

All environment-specific operational values are supplied by the caller. The harness does not assume service names, ports, listener addresses, executable locations, CSMS data directories, or control-socket paths.

Example shape:

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

`--idle-confirmed` is an explicit operator assertion for the pre-takeover state. Chunk 1 does not attempt to infer transaction state from a legacy backend with an unrelated storage model.

A successful preflight records:

- `state.json`: authoritative field-run configuration and phase;
- `preflight.json`: individual preflight decisions.

Reusing a run directory with different operational configuration is rejected.

Inspect stored state with:

```sh
python -m field.harness status /path/to/run
```

Service takeover and rollback are intentionally deferred to the next implementation chunk.
