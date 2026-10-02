# OCPP CSMS

A deliberately small Python OCPP 1.6J CSMS intended to run as an appliance-style service.

Its default policy is simple: **accept chargers, avoid blocking charging, preserve evidence, and expose a small diagnostic surface.** The implementation stays intentionally direct so operational behavior remains easy to inspect.

## Quick start

From a repository checkout:

```bash
sh install.sh
```

Do not run the installer itself with `sudo`.

The installed service listens on `0.0.0.0:9000` by default, starts at boot, and restarts on failure.

Useful commands:

```bash
ocpp-csms status
ocpp-csms status --charging
ocpp-csms events
sudo systemctl status ocpp-csms
sudo systemctl restart ocpp-csms
sudo journalctl -u ocpp-csms -f
```

To choose the listener endpoint during installation:

```bash
sh install.sh --host 0.0.0.0 --port 8888
```

or:

```bash
OCPP_CSMS_HOST=0.0.0.0 OCPP_CSMS_PORT=8888 sh install.sh
```

The selected host and port are written into the installed systemd service and reused after reboot.

## Operating model

The CSMS is deliberately permissive by default:

- unknown charge-point identities are accepted;
- RFID authorization is accepted by default;
- a charger is not rejected solely because it did not negotiate the expected `ocpp1.6` WebSocket subprotocol;
- operational anomalies are preserved as evidence instead of automatically becoming charging blockers.

The design principle is: **observe aggressively; block reluctantly.**

## Network and trust boundary

The built-in listener is intended for a trusted charger LAN or equivalent private network boundary. It uses plain `ws://`; it does not terminate TLS or authenticate clients itself.

**Do not expose the built-in listener directly to an untrusted network or the public Internet.**

When chargers must reach the CSMS across a broader network, put an appropriate boundary in front of it, such as:

- a private VPN or WireGuard network;
- a TLS-terminating reverse proxy exposing `wss://`;
- an equivalent private routed network, firewall, or gateway.

Charger allowlists, RFID deny policies, client certificates, or mandatory strict admission are intentionally not part of the default appliance behavior. Add them only as opt-in deployment policy when a real environment requires them.

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

`init` creates the SQLite database and transaction archive. `status` is read-only. `events` reads the recorded OCPP/runtime timeline. `explain` presents the same evidence for one charger and incident window; it does not infer a root cause.

Examples:

```bash
ocpp-csms events charger-01 --since 2026-10-01T20:00:00Z --until 2026-10-01T21:00:00Z
ocpp-csms explain charger-01 --at 2026-10-01T20:35:00Z
ocpp-csms explain charger-01 --since 2026-10-01T20:30:00Z --until 2026-10-01T20:45:00Z
```

All commands accept `--data-dir PATH` before the command name. Diagnostic timestamps accept ISO-8601; timestamps without an offset are treated as UTC.

## Transaction recovery

Queued OCPP 1.6 transaction traffic can outlive the CSMS instance that originally issued its transaction ID. The recovery rules are:

- a retried `StartTransaction` that was never acknowledged follows the normal bounded idempotency path;
- queued `MeterValues` or `StopTransaction` for an unknown transaction ID recover that exact historical ID when it is free locally;
- recovered historical IDs do not advance the normal local transaction-ID sequence;
- later queued evidence for the same recovered ID is attached to that recovered transaction;
- an unknown historical `StopTransaction` creates a recovered **stopped** record, not a synthetic open session;
- if historical traffic conflicts with an unrelated local transaction using the same numeric ID, the CSMS does not merge or overwrite either transaction. It preserves the incoming frame separately as unresolved evidence.

Concrete collision evidence currently includes a different charge point, a different connector when both are known, or an incoming transaction timestamp that predates the local transaction start.

Recovery decisions are also written as structured runtime events, including historical adoption, continued recovered evidence, transaction-ID collision, and unresolved-message preservation.

## Data and evidence

By default the appliance stores data under:

```text
~/ocpp-csms-data/
  ocpp-csms.sqlite3
  transactions/
    YYYY-MM-DD/
      <charger>-<transaction>.json
  transactions-unresolved/
    YYYY-MM-DD/
      <charger>-<transaction>-<message>-<suffix>.json
```

The JSON transaction archive remains directly readable and copyable. SQLite stores append-oriented OCPP evidence, runtime events, and small derived operational state used by status/diagnostic commands.

Incoming OCPP requests and handled replies are recorded, together with connection lifecycle and recovery diagnostics. Unresolved transaction collisions are preserved outside the normal transaction archive so they cannot silently mutate an unrelated transaction.

## Installation details

The application runs as the installing user, not root. The installer uses `sudo` only to place and enable `/etc/systemd/system/ocpp-csms.service`.

```text
~/.local/share/ocpp-csms/venv/   private Python environment
~/.local/bin/ocpp-csms           stable command
~/ocpp-csms-data/                user-owned appliance data
/etc/systemd/system/ocpp-csms.service
```

The installer initializes appliance storage before touching systemd, then validates the command, writable data directory, SQLite database, transaction archive, service state, and listening endpoint.

To use another user-owned data directory:

```bash
OCPP_CSMS_DATA_DIR="$HOME/my-csms-data" sh install.sh
```

## Development and tests

Install development dependencies and run the test suite with:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

CI exercises the project on the appliance target and a newer compatibility target:

- Debian 12 Bookworm / Python 3.11 / ARM64;
- Debian 13 / Python 3.13 / ARM64.

Tests are organized by behavior rather than framework layer where practical. Recovery tests deliberately cover both archive-level invariants and session/SQLite integration so restart, collision, and synthetic-session regressions remain visible.

## Source layout

```text
src/ocpp_csms/
  app.py           # CLI and process startup
  server.py        # WebSocket accept loop and connection lifecycle
  session.py       # direct OCPP 1.6J handlers
  events.py        # SQLite event store and derived state
  diagnostics.py   # direct event queries and formatting
  status.py        # status queries and formatting
  transactions.py  # JSON transaction archive and recovery
  time.py          # timestamp helper
systemd/
  ocpp-csms.service.in
```

This README is the project documentation. For a project this small, important operational information should remain here rather than being split across a separate documentation tree.
