# OCPP CSMS

A deliberately small Python OCPP 1.6J CSMS intended to run as an appliance-style service.

Its default policy is simple: **accept chargers, avoid blocking charging, preserve evidence, and expose a small diagnostic and control surface.** The implementation stays intentionally direct so operational behavior remains easy to inspect.

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
ocpp-csms txn --active
ocpp-csms txn --last
ocpp-csms txn 42 --events
ocpp-csms events
ocpp-csms start charger-01 --cp 1 --id-tag REMOTE
ocpp-csms stop charger-01 --txn 42
ocpp-csms reboot charger-01
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

Operator control commands do not open another TCP service. The CLI talks to the running daemon through a Unix-domain socket named `control.sock` inside the configured data directory. The socket is created mode `0660`, so local filesystem ownership and permissions are the control boundary.

## Commands

Run `ocpp-csms`, `ocpp-csms help`, or `ocpp-csms --help` to show commands and parameters.

```text
ocpp-csms init
ocpp-csms serve [--host HOST] [--port PORT] [--log-level LEVEL]
ocpp-csms status [CHARGER]
ocpp-csms status --charging
ocpp-csms transactions [ID] [--active|--last] [--charger CHARGER] [--connector N|--cp N] [--events]
ocpp-csms txn [ID] [--active|--last] [--charger CHARGER] [--connector N|--cp N] [--events]
ocpp-csms start CHARGER [--connector N|--cp N] --id-tag TAG
ocpp-csms stop CHARGER --transaction ID|--txn ID
ocpp-csms reboot CHARGER [--hard]
ocpp-csms events [CHARGER] [--since TIME] [--until TIME] [--limit N]
ocpp-csms explain CHARGER --at TIME [--minutes N]
ocpp-csms explain CHARGER --since TIME --until TIME
```

`init` creates the SQLite database and transaction archive. `status` is read-only. `transactions` is the canonical read-only transaction inspector and `txn` is its exact alias. It never starts, stops, closes, repairs, or deletes a transaction. `events` reads the recorded OCPP/runtime timeline. `explain` presents the same evidence for one charger and incident window; it does not infer a root cause.

Transaction inspection defaults to recent transactions newest first. `--active` shows unfinished transactions; `--last` shows the newest matching non-active transaction, so an active transaction and `--last` are never the same record. A positional transaction ID opens a detailed read-only view. List filters include `--charger`, `--connector` / `--cp`, `--id-tag`, `--since`, `--until`, and `--limit`. Add `--events` to a transaction ID to append its transaction-scoped OCPP timeline.

`status --charging` uses the same archived transaction activity model as `txn --active`, so both commands agree on which chargers have unfinished transactions. SQLite remains the source for live connector/status detail.

The lowercase control aliases are equivalent to their long forms: `--cp` is an alias for `--connector`, and `--txn` is an alias for `--transaction`.

Examples:

```bash
ocpp-csms txn
ocpp-csms txn --active
ocpp-csms txn --last
ocpp-csms txn --charger charger-01 --last
ocpp-csms txn --cp 1 --active
ocpp-csms txn 17
ocpp-csms txn 17 --events
ocpp-csms start charger-01 --cp 1 --id-tag REMOTE
ocpp-csms stop charger-01 --txn 42
ocpp-csms reboot charger-01
ocpp-csms reboot charger-01 --hard
ocpp-csms events charger-01 --since 2026-10-01T20:00:00Z --until 2026-10-01T21:00:00Z
ocpp-csms explain charger-01 --at 2026-10-01T20:35:00Z
ocpp-csms explain charger-01 --since 2026-10-01T20:30:00Z --until 2026-10-01T20:45:00Z
```

All commands accept `--data-dir PATH` before the command name. Diagnostic timestamps accept ISO-8601; timestamps without an offset are treated as UTC.

Control-command exit behavior is intentionally simple:

- OCPP `Accepted` returns exit status `0`;
- OCPP `Rejected`, disconnected chargers, and control-socket failures return exit status `1`;
- invalid command-line arguments use normal `argparse` behavior and return exit status `2`.

## Remote charger control

The control path stays inside the daemon that already owns the live charger WebSocket:

```text
ocpp-csms CLI
    |
    | Unix socket: <data-dir>/control.sock
    v
running CSMS daemon
    |
    | current ChargePointSession
    v
charger WebSocket
```

The supported OCPP 1.6J mappings are:

- `start` -> `RemoteStartTransaction`;
- `stop` -> `RemoteStopTransaction`;
- `reboot` -> `Reset` with `Soft` by default and `Hard` when `--hard` is used.

Commands are only sent to a charger that is currently connected. They are not queued for later delivery.

A remote command confirmation and an actual transaction state change are deliberately treated as different facts. `RemoteStartTransaction.conf(status=Accepted)` means the charger accepted the request; it does **not** create a local transaction. The transaction is created only when the charger later sends `StartTransaction`. Likewise, `RemoteStopTransaction.conf(status=Accepted)` does **not** mark the transaction stopped; the transaction becomes stopped only when the charger later sends `StopTransaction`.

The event stream preserves both sides of each remote command. A CSMS-initiated request is recorded as `out`, and the charger's confirmation is recorded as `in`. For example, a successful remote-start sequence can appear as:

```text
out RemoteStartTransaction
in  RemoteStartTransaction
in  StartTransaction
out StartTransaction
```

This distinction is intentional so `events` and `explain` can answer separately whether the CSMS issued a command, whether the charger accepted it, and whether the charger actually changed transaction state.

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
  control.sock
  ocpp-csms.sqlite3
  transactions/
    YYYY-MM-DD/
      <charger>-<transaction>.json
  transactions-unresolved/
    YYYY-MM-DD/
      <charger>-<transaction>-<message>-<suffix>.json
```

`control.sock` exists only while the daemon is running and is removed on shutdown. The JSON transaction archive remains directly readable and copyable. SQLite stores append-oriented OCPP evidence, runtime events, and small derived operational state used by status/diagnostic commands.

Incoming OCPP requests and handled replies are recorded together with CSMS-initiated remote requests and charger confirmations. Connection lifecycle and recovery diagnostics are also preserved. Unresolved transaction collisions are stored outside the normal transaction archive so they cannot silently mutate an unrelated transaction.

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

The daemon and control CLI must use the same data directory because that directory determines the local control-socket path.

## Development and tests

Install development dependencies and run the test suite with:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

Pytest reports the 10 slowest test phases taking at least 10 ms on every run. This applies locally and in CI, so test-duration regressions remain visible without changing the test command.

CI exercises the project on the appliance target and a newer compatibility target:

- Debian 12 Bookworm / Python 3.11 / ARM64;
- Debian 13 / Python 3.13 / ARM64.

Tests are organized around behavior and state invariants rather than exact human-readable wording. Recovery tests deliberately cover both archive-level invariants and session/SQLite integration so restart, collision, and synthetic-session regressions remain visible.

Remote-control tests cover outbound OCPP payloads, live-session replacement, Unix-socket dispatch, CLI request/exit behavior, and the key state invariant: an accepted remote start or stop command does not itself create or close a transaction. Separate evidence tests verify that requests and confirmations are preserved with the correct `out`/`in` direction.

## Source layout

```text
src/ocpp_csms/
  app.py               # CLI and process startup
  control.py           # local Unix-socket control protocol and client
  server.py            # WebSocket accept loop and connection lifecycle
  session.py           # direct OCPP 1.6J handlers and outbound commands
  events.py            # SQLite event store and derived state
  diagnostics.py       # direct event queries and formatting
  status.py            # status queries and formatting
  transaction_query.py # read-only transaction query/model layer
  transaction_cli.py   # transaction list/detail formatting
  transactions.py      # JSON transaction archive and recovery
  time.py              # timestamp helper
systemd/
  ocpp-csms.service.in
```

This README is the project documentation. For a project this small, important operational information should remain here rather than being split across a separate documentation tree.
