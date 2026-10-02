# OCPP CSMS

A deliberately small Python OCPP 1.6J CSMS.

The first version keeps protocol handling direct and permissive. It is intended to become an appliance-style service: accept chargers, avoid blocking charging, preserve evidence, and expose only a small diagnostic surface.

## Install the appliance

From the repository checkout:

```bash
sh install.sh
```

The application runs as the installing user, not root. The installer uses `sudo` only to place and enable `/etc/systemd/system/ocpp-csms.service`.

```text
~/.local/share/ocpp-csms/venv/   private Python environment
~/.local/bin/ocpp-csms           stable command
~/ocpp-csms-data/                user-owned appliance data
/etc/systemd/system/ocpp-csms.service
```

The installer explicitly initializes the appliance storage before touching systemd, then validates the command, writable data directory, SQLite database, transaction archive, service state, and listening port. The service starts at boot and restarts on failure.

Useful commands:

```bash
ocpp-csms status
sudo systemctl status ocpp-csms
sudo systemctl restart ocpp-csms
sudo journalctl -u ocpp-csms -f
```

Do not run the installer itself with sudo. To use another user-owned data directory:

```bash
OCPP_CSMS_DATA_DIR="$HOME/my-csms-data" sh install.sh
```

## Commands

Run `ocpp-csms`, `ocpp-csms help`, or `ocpp-csms --help` to show commands and parameters.

```text
ocpp-csms init
ocpp-csms serve [--host HOST] [--port PORT] [--log-level LEVEL]
ocpp-csms status [CHARGER]
ocpp-csms status --charging
ocpp-csms events [CHARGER] [--since TIME] [--until TIME] [--limit N]
ocpp-csms explain CHARGER --at TIME [--minutes N]
ocpp-csms explain CHARGER --since TIME --until TIME
```

`init` creates the SQLite database and transaction archive. `status` remains read-only. `events` reads the recorded OCPP/runtime timeline. `explain` is the same evidence view constrained to one charger and a selected incident window; it does not infer a root cause.

```bash
ocpp-csms events charger-01 --since 2026-10-01T20:00:00Z --until 2026-10-01T21:00:00Z
ocpp-csms explain charger-01 --at 2026-10-01T20:35:00Z
ocpp-csms explain charger-01 --since 2026-10-01T20:30:00Z --until 2026-10-01T20:45:00Z
```

All commands accept `--data-dir PATH` before the command name. Diagnostic timestamps accept ISO-8601; timestamps without an offset are treated as UTC.

## Data

```text
~/ocpp-csms-data/
  ocpp-csms.sqlite3
  transactions/
    YYYY-MM-DD/
      <charger>-<transaction>.json
```

Existing `events.sqlite3` files are renamed to `ocpp-csms.sqlite3` when the appliance storage is next opened. The JSON transaction archive remains directly readable and copyable. SQLite is the append-only operational evidence index. OCPP requests and handled replies are recorded, along with server and charger connection lifecycle events.

## Layout

```text
src/ocpp_csms/
  app.py           # CLI and process startup
  server.py        # WebSocket accept loop and connection lifecycle
  session.py       # direct OCPP 1.6J handlers
  events.py        # SQLite event store
  diagnostics.py   # direct event queries and formatting
  status.py        # status queries and formatting
  transactions.py  # JSON transaction archive
  time.py          # timestamp helper
systemd/
  ocpp-csms.service.in
```
