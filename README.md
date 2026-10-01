# OCPP CSMS

A deliberately small Python OCPP 1.6J CSMS.

The first version keeps protocol handling direct and permissive. It is intended to become an appliance-style service: accept chargers, avoid blocking charging, preserve evidence, and expose only a small diagnostic surface.

## Install for one user

No root privileges are required. From the repository checkout:

```bash
sh install.sh
```

The installer creates a private virtual environment under `~/.local/share/ocpp-csms/` and exposes the stable command at:

```text
~/.local/bin/ocpp-csms
```

Add `~/.local/bin` to `PATH` if it is not already present. Shell scripts can also call that full path directly, so activating the Python environment is never required.

## Commands

Run `ocpp-csms`, `ocpp-csms help`, or `ocpp-csms --help` to show the available commands and parameters.

```text
ocpp-csms serve [--host HOST] [--port PORT] [--log-level LEVEL]
ocpp-csms status [CHARGER]
ocpp-csms status --charging
```

Start the OCPP server with:

```bash
ocpp-csms serve
```

By default the server listens on `0.0.0.0:9000` and accepts OCPP 1.6J charge points at:

```text
ws://localhost:9000/{charge_point_id}
```

Check the appliance and all known chargers with:

```bash
ocpp-csms status
```

Check one charger or only chargers that appear to be charging with:

```bash
ocpp-csms status charger-01
ocpp-csms status --charging
```

All commands accept `--data-dir PATH` before the command name when another writable data location is needed.

## Data

Data is stored under the login user's home directory by default:

```text
~/ocpp-csms-data/
  events.sqlite3
  transactions/
    YYYY-MM-DD/
      <charger>-<transaction>.json
```

The JSON transaction archive is intended to stay directly readable and copyable even if the database or application is unavailable. SQLite is an append-only operational evidence index for OCPP messages and runtime lifecycle events. It uses Python's standard-library `sqlite3` module and no ORM.

## Layout

```text
src/ocpp_csms/
  app.py           # CLI and process startup
  server.py        # WebSocket accept loop and connection lifecycle
  session.py       # direct OCPP 1.6J handlers
  events.py        # small SQLite event store
  status.py        # direct status queries and formatting
  transactions.py  # human-readable JSON transaction archive
  time.py          # timestamp helper
```

Persistence and appliance diagnostics are kept as explicit, small components rather than through another dispatch, ORM, or service framework.
