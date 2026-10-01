# OCPP CSMS

A deliberately small Python OCPP 1.6J CSMS.

The first version keeps protocol handling direct and permissive. It is intended to become an appliance-style service: accept chargers, avoid blocking charging, preserve evidence, and expose only a small diagnostic surface.

## Run

```bash
python -m pip install -e ".[dev]"
ocpp-csms
```

By default the server listens on `0.0.0.0:9000` and accepts OCPP 1.6J charge points at:

```text
ws://localhost:9000/{charge_point_id}
```

Data is stored under the login user's home directory by default:

```text
~/ocpp-csms-data/
  events.sqlite3
  transactions/
    YYYY-MM-DD/
      <charger>-<transaction>.json
```

The JSON transaction archive is intended to stay directly readable and copyable even if the database or application is unavailable. SQLite is an append-only operational evidence index for OCPP messages and runtime lifecycle events. It uses Python's standard-library `sqlite3` module and no ORM.

Use `--data-dir PATH` to choose another writable location.

## Layout

```text
src/ocpp_csms/
  app.py           # process startup
  server.py        # WebSocket accept loop and connection lifecycle
  session.py       # direct OCPP 1.6J handlers
  events.py        # small SQLite event store
  transactions.py  # human-readable JSON transaction archive
  time.py          # timestamp helper
```

Persistence and appliance diagnostics are kept as explicit, small components rather than through another dispatch, ORM, or service framework.
