# OCPP CSMS

A deliberately small Python OCPP 1.6J CSMS.

The first version keeps protocol handling direct and permissive. It is intended to become an appliance-style service: accept chargers, avoid blocking charging, preserve evidence, and expose only a small diagnostic surface.

## Install the appliance

From the repository checkout:

```bash
sh install.sh
```

The application itself runs as the user who launches the installer, not as root. The installer uses `sudo` only to place and enable the systemd unit under `/etc/systemd/system/`.

The install creates:

```text
~/.local/share/ocpp-csms/venv/   private Python environment
~/.local/bin/ocpp-csms           stable command
~/ocpp-csms-data/                user-owned appliance data
/etc/systemd/system/ocpp-csms.service
```

Before installing the service, the script verifies that the command is executable, the data directory is writable, and SQLite can initialize successfully. It then installs the unit, enables it at boot, starts it immediately, and verifies that systemd reports it active. If startup fails, the installer prints the recent service journal.

The generated unit explicitly uses the installing user's account and home directory, starts after the network is online, and restarts automatically after failure.

Useful commands after installation:

```bash
ocpp-csms status
sudo systemctl status ocpp-csms
sudo systemctl restart ocpp-csms
sudo journalctl -u ocpp-csms -f
```

Add `~/.local/bin` to `PATH` if it is not already present. Shell scripts can also call `~/.local/bin/ocpp-csms` directly, so activating the Python environment is never required.

To use a different user-owned data location during installation:

```bash
OCPP_CSMS_DATA_DIR="$HOME/my-csms-data" sh install.sh
```

## Commands

Run `ocpp-csms`, `ocpp-csms help`, or `ocpp-csms --help` to show the available commands and parameters.

```text
ocpp-csms serve [--host HOST] [--port PORT] [--log-level LEVEL]
ocpp-csms status [CHARGER]
ocpp-csms status --charging
```

Start the OCPP server manually with:

```bash
ocpp-csms serve
```

Normally the installed systemd service starts it automatically. By default the server listens on `0.0.0.0:9000` and accepts OCPP 1.6J charge points at:

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
systemd/
  ocpp-csms.service.in  # unprivileged appliance service template
```

Persistence and appliance diagnostics are kept as explicit, small components rather than through another dispatch, ORM, or service framework.
