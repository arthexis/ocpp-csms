# OCPP CSMS

A deliberately small, permissive Python OCPP 1.6J central system intended to be operated as an appliance. The default objective is to **accept chargers, avoid unnecessary charging interruptions, preserve evidence, and provide simple diagnostics and controls**.

## Operating model

- Unknown charge-point identities can connect by default; local policy and public authentication must be configured appropriately.
- RFID is accepted by default unless an explicit `rfid.csv` allow list is configured.
- SQLite records observations and transactions; diagnostics should never invent physical charger state.
- OCPP request acceptance is not proof of physical execution.

## Install

Run as a non-root operator on the target host:

```sh
./deploy.sh
ocpp-csms status
ocpp-csms --help
```

Installation uses `ansible/playbooks/install.yml`; Discover is installed when `eth0` exists. Public WSS exposure is **a separate authenticated deployment step**. See [operations](docs/operations.md#installation-exposure-and-enrollment) before exposing a port or enrolling a production charger.

## First diagnostics

```sh
ocpp-csms status
ocpp-csms inspect --all
ocpp-csms events --since 1h
ocpp-csms --help
```

Use `ocpp-csms COMMAND --help` for the current command-specific syntax. Investigate a particular charger with `inspect --cp ID` and review transaction evidence before intervening. For field response procedures see [operations](docs/operations.md#evidence-first-incident-workflow).

## Documentation

- [Operations](docs/operations.md): installation, TLS and authentication, CLI operations, RFID, Discover, maintenance, incident response and safe data reset.
- [Architecture](docs/architecture.md): persisted evidence, transaction semantics, schema lifecycle and deployment invariants.
- [Development](docs/development.md): source layout, local tests, CI, and documentation verification.

The running service and source code are authoritative for exact flags and runtime behavior; these documents provide context and recovery knowledge. Never substitute a historical CLI example for `--help` on the deployed version.
