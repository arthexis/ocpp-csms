# OCPP CSMS

A deliberately small Python OCPP 1.6J CSMS intended to run as an appliance-style service.

Its default policy is simple: **accept chargers, avoid blocking charging, preserve evidence, and expose a small diagnostic and control surface.** The implementation stays intentionally direct so operational behavior remains easy to inspect.

## Quick start

From a repository checkout:

```bash
sh install.sh
```

Do not run the installer itself with `sudo`. The installed `ocpp-csms.service` runs as the installing user, listens on `0.0.0.0:9000` by default, starts at boot, and restarts on failure.

Before changing an existing appliance, the installer performs a read-only usage preflight. An active transaction always blocks installation. If the current CSMS has connected but idle chargers, installation also stops unless the operator explicitly authorizes an idle handoff with:

```bash
sh install.sh --rollover
```

`--rollover` never overrides active charging. It only authorizes replacement of an in-use but idle CSMS; the staged handoff mechanics are kept separate from this safety policy.

Check the appliance with:

```bash
ocpp-csms status
sudo systemctl status ocpp-csms
sudo journalctl -u ocpp-csms -f
```

If the charger is already configured for this CSMS, that is all that is required.

## Installation and OCPP Discover

Choose another listener endpoint during installation with:

```bash
sh install.sh --host 0.0.0.0 --port 8888
```

or equivalent environment values:

```bash
OCPP_CSMS_HOST=0.0.0.0 OCPP_CSMS_PORT=8888 sh install.sh
```

The selected host and port are written into the installed systemd service and reused after reboot. Use `OCPP_CSMS_DATA_DIR` to choose another user-owned data directory.

### Optional network discovery

Some field chargers remember an old server or gateway address and cannot simply be pointed at the new CSMS. OCPP Discover is an optional network-bootstrap helper for those cases.

Install the appliance together with discovery:

```bash
sh install.sh --with-discover
```

This keeps the normal CSMS unprivileged and installs a separate root `ocpp-discover.service`. At boot, Discover first gives the charger a short chance to connect normally. If no charger appears, it passively watches for validated plaintext WebSocket traffic to an IPv4 address already owned by the selected Ethernet interface. When that existing endpoint is visible, Discover applies only the narrow source/destination/port redirect needed to reach the local CSMS and does not claim another address. If no usable local endpoint is visible, it falls back to repeated unresolved-ARP discovery, temporarily claims only that exact IPv4 address as an additive `/32`, observes the charger's destination and port, installs the narrow redirect, and stops once the CSMS reports a real charger session.

Discover defaults to `eth0`. Its standalone management surface is:

```bash
./discover.sh                         # run discovery now
./discover.sh --interface eno1       # use another Ethernet interface
./discover.sh --install              # install and enable discovery at boot
./discover.sh --cleanup              # remove discovery-owned network state
./discover.sh --uninstall            # disable/remove discovery and clean its state
```

`discover.sh --install` installs missing Debian runtime dependencies when necessary:

```text
tcpdump
nftables
iproute2
```

A plain `./discover.sh` never installs packages automatically; if dependencies are missing it prints the Debian installation command instead.

The base installer deliberately treats discovery as an optional feature:

```bash
sh install.sh --with-discover       # install/enable it
sh install.sh --without-discover    # disable/remove it
sh install.sh                       # preserve its current enabled/disabled state
```

Discovery only redirects **validated plaintext HTTP WebSocket Upgrade traffic**. TLS/WSS traffic is detected as opaque and refused rather than guessed. The invariant is to apply the minimum network mutation necessary: an existing host address is never re-claimed or removed, while the ARP fallback owns and later cleans only the exact `/32` it added. Failed attempts roll back only state created by that attempt. Details and field-only primitives live in `field/README.md`.

## Operating model

The CSMS is deliberately permissive by default:

- unknown charge-point identities are accepted;
- RFID authorization is accepted by default;
- a charger is not rejected solely because it did not negotiate the expected `ocpp1.6` WebSocket subprotocol;
- operational anomalies are preserved as evidence instead of automatically becoming charging blockers.

The design principle is: **observe aggressively; block reluctantly.**

The WebSocket listener uses a single direct event loop. Normal OCPP handling writes evidence after each handler completes; there is no Celery worker, async queue, or desired-state engine behind the protocol path.

## Network and trust boundary

The built-in listener is intended for a trusted charger LAN or equivalent private network boundary. It uses plain `ws://`; it does not terminate TLS or authenticate clients itself.

**Do not expose the built-in listener directly to an untrusted network or the public Internet.**

When chargers must reach the CSMS across a broader network, put an appropriate boundary in front of it, such as a private VPN/WireGuard network, a TLS-terminating reverse proxy exposing `wss://`, or an equivalent private routed network/firewall.

OCPP Discover does not change this trust model. It is a local bootstrap mechanism for a charger-facing Ethernet network, not a public-network interception service.

Operator control commands do not open another TCP service. The CLI talks to the running daemon through `<data-dir>/control.sock`, a Unix-domain socket created mode `0660`. Local filesystem ownership and permissions are therefore the control boundary.

## Commands

Run `ocpp-csms`, `ocpp-csms help`, or `ocpp-csms --help` for the current command surface.

```text
ocpp-csms init
ocpp-csms serve [--host HOST] [--port PORT] [--log-level LEVEL]
ocpp-csms status [CHARGER]
ocpp-csms status --charging
ocpp-csms transactions [ID] [--active|--last] [--charger CHARGER] [--connector N|--cp N] [--events]
ocpp-csms txn [ID] [--active|--last] [--charger CHARGER] [--connector N|--cp N] [--events]
ocpp-csms config CHARGER [KEY ...] [-f|--force]
ocpp-csms profile list
ocpp-csms profile help TEMPLATE
ocpp-csms profile set max-power --watts WATTS [--charger CHARGER]
ocpp-csms profile composite [--charger CHARGER] [--connector N|--cp N] [--duration SECONDS] [--json]
ocpp-csms profile clear [--charger CHARGER] [--id ID] [--connector N|--cp N] [--purpose PURPOSE] [--stack-level N]
ocpp-csms start CHARGER [--connector N|--cp N] --id-tag TAG
ocpp-csms stop CHARGER (--transaction ID|--txn ID)
ocpp-csms reboot CHARGER [--hard]
ocpp-csms events [CHARGER] [--since TIME] [--until TIME] [--limit N]
ocpp-csms explain CHARGER --at TIME [--minutes N]
ocpp-csms explain CHARGER --since TIME --until TIME
```

All commands accept `--data-dir PATH` before the command name. Diagnostic timestamps accept ISO-8601; timestamps without an offset are treated as UTC. `--cp` is the lowercase alias for `--connector`; `--txn` is the lowercase alias for `--transaction`.

`init` creates the SQLite database and JSON transaction archive. `status` is read-only. `transactions` is the canonical transaction inspector and `txn` is its exact alias. `events` reads recorded OCPP/runtime evidence, while `explain` presents the same evidence for one charger and incident window without inventing a root cause.

Control-command exit behavior is intentionally simple:

- OCPP `Accepted` and successful queries return `0`;
- OCPP rejection, disconnected chargers, guarded operations, and control-socket failures return `1`;
- invalid command-line arguments use normal `argparse` behavior and return `2`.

## Transactions and status

Transaction inspection defaults to recent transactions newest first. `--active` shows unfinished transactions; `--last` shows the newest matching non-active transaction. A positional transaction ID opens the detailed read-only view. Add `--events` to append its transaction-scoped OCPP timeline.

```bash
ocpp-csms txn
ocpp-csms txn --active
ocpp-csms txn --last
ocpp-csms txn --charger charger-01 --last
ocpp-csms txn --cp 1 --active
ocpp-csms txn 17 --events
```

`status --charging` uses the same archived transaction-activity model as `txn --active`, so the two commands agree on which chargers have unfinished transactions. SQLite remains the source for live connector/status detail.

A remote command confirmation and an actual transaction state change are separate facts. An accepted `RemoteStartTransaction` does not create a transaction; the transaction appears only when the charger sends `StartTransaction`. An accepted remote stop likewise does not close the transaction until `StopTransaction` arrives.

## Remote charger control

The control path stays inside the daemon that owns the live charger WebSocket:

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

Supported mappings include:

- `config` -> `GetConfiguration`;
- `profile set` -> `SetChargingProfile`;
- `profile composite` -> `GetCompositeSchedule`;
- `profile clear` -> `ClearChargingProfile`;
- `start` -> `RemoteStartTransaction`;
- `stop` -> `RemoteStopTransaction`;
- `reboot` -> `Reset`, Soft by default and Hard with `--hard`.

Commands are only sent to chargers currently connected to this process; they are not queued for later delivery.

Examples:

```bash
ocpp-csms start charger-01 --cp 1 --id-tag REMOTE
ocpp-csms stop charger-01 --txn 42
ocpp-csms reboot charger-01
ocpp-csms reboot charger-01 --hard
```

The event stream preserves both the outbound CSMS request and inbound charger confirmation so diagnostics can distinguish “command issued,” “command accepted,” and “charger actually changed state.”

## GetConfiguration safety policy

`config` sends live OCPP `GetConfiguration` and displays what the charger reports. The CSMS does not coerce values, infer defaults, or maintain a desired configuration model.

```bash
ocpp-csms config charger-01
ocpp-csms config charger-01 HeartbeatInterval MeterValueSampleInterval
```

Because some field chargers behave unreliably when configuration traffic is interleaved with transaction traffic, `GetConfiguration` is blocked by default when that charger has an active transaction. This is appliance safety policy, not an OCPP protocol requirement. `-f` / `--force` bypasses the guard when the operator deliberately accepts that risk.

```bash
ocpp-csms config charger-01 HeartbeatInterval --force
```

Blocked and forced decisions are preserved as runtime evidence; actual requests and confirmations are preserved as normal OCPP events.

## Smart Charging

Smart Charging is intentionally **stateless on the CSMS side**. The charger owns its installed profiles and is the source of truth; site/business policy belongs in an upper layer that invokes these commands when needed.

The current built-in template is `max-power`:

```bash
ocpp-csms profile list
ocpp-csms profile help max-power
ocpp-csms profile set max-power --watts 60000
```

It sends a station-wide `ChargePointMaxProfile` on connector `0`, profile ID `1`, stack level `0`, Absolute kind, with a watt-based schedule beginning at period `0`.

`profile composite` uses `GetCompositeSchedule` to inspect the **effective schedule** reported by the charger:

```bash
ocpp-csms profile composite
ocpp-csms profile composite --cp 1 --duration 7200
ocpp-csms profile composite --json
```

Some chargers accept station-wide profiles on connector `0` but reject `GetCompositeSchedule` there. In that case the CSMS preserves the rejected connector-0 attempt and queries each known physical connector. A fully successful fan-out is reported as a compatibility fallback; the CSMS never fabricates a station-level aggregate from those schedules.

`profile clear` sends `ClearChargingProfile` and accepts the OCPP filters exposed by the CLI:

```bash
ocpp-csms profile clear
ocpp-csms profile clear --id 7
ocpp-csms profile clear --cp 1
ocpp-csms profile clear --purpose ChargePointMaxProfile
ocpp-csms profile clear --stack-level 2
```

These Smart Charging commands use the same live-session control path and evidence recording as other outbound commands. They are not blocked merely because a transaction is active.

## Transaction recovery

Queued OCPP 1.6 traffic can outlive the CSMS instance that originally issued its transaction ID. Recovery preserves evidence without silently merging unrelated activity:

- a retried `StartTransaction` that was never acknowledged follows bounded idempotency handling;
- queued `MeterValues` or `StopTransaction` for an unknown historical ID can adopt that exact ID when it is free locally;
- recovered historical IDs do not advance the normal local ID sequence;
- later evidence for the same recovered ID attaches to that recovered transaction;
- an unknown historical `StopTransaction` creates a recovered stopped record rather than a synthetic open session;
- collisions with an unrelated local transaction are kept unresolved instead of overwriting either transaction.

Collision evidence includes a different charge point, a different connector when both are known, or an incoming transaction timestamp that predates the local transaction start. Recovery decisions are also recorded as structured runtime events.

## Data and evidence

By default the appliance stores:

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

`control.sock` exists only while the daemon is running. JSON archives remain directly readable and copyable. SQLite stores append-oriented OCPP evidence, runtime events, transaction state, connector status, and the small amount of operational state needed by diagnostics.

Incoming OCPP calls and replies are preserved together with CSMS-initiated requests and charger confirmations. Connection lifecycle, configuration guard decisions, Smart Charging evidence, transaction recovery, and unresolved collisions are recorded rather than reduced to human-readable logs.

## Installed layout

The normal appliance runs as the installing user, not root. The base installer uses `sudo` only for systemd installation/management.

```text
~/.local/share/ocpp-csms/venv/       private Python environment
~/.local/bin/ocpp-csms               stable command
~/ocpp-csms-data/                    user-owned appliance data
/etc/systemd/system/ocpp-csms.service
```

When OCPP Discover is installed, its small field runtime is copied under the appliance prefix and the separate root service is added:

```text
~/.local/share/ocpp-csms/discover/
/etc/systemd/system/ocpp-discover.service
/run/ocpp-discover/                  transient discovery receipts/state
```

Uninstalling Discover does not uninstall the CSMS or remove `tcpdump`, `nftables`, or `iproute2` packages that may be used elsewhere.

## Development and tests

Install development dependencies and run the normal suite with:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

Field-harness self-tests are explicit:

```bash
python -m pytest field/tests
```

CI exercises:

- Debian 12 Bookworm / Python 3.11 / ARM64 as the primary appliance target;
- Debian 13 / Python 3.13 / ARM64 as the compatibility target.

Tests favor behavior and state invariants over exact human-readable output. Installer tests exercise the public `--help` surfaces and verify privilege/delegation boundaries. Discovery tests cover the already-connected fast path, passive detection of a validated WebSocket endpoint already owned by the host, redirect-only success/rollback for that case, passive ARP fallback, additive address ownership, charger-specific WebSocket discovery after the claim, port-aware nftables redirects, and exact cleanup of only discovery-owned state.

## Source layout

```text
src/ocpp_csms/
  app.py               # CLI and process startup
  control.py           # local Unix-socket control protocol and client
  profile_templates.py # built-in stateless Smart Charging templates
  server.py            # WebSocket accept loop and connection lifecycle
  session.py           # direct OCPP 1.6J handlers and outbound commands
  events.py            # SQLite event store and derived state
  diagnostics.py       # direct event queries and formatting
  status.py            # status queries and formatting
  transaction_query.py # read-only transaction query/model layer
  transaction_cli.py   # transaction list/detail formatting
  transactions.py      # JSON transaction archive and recovery
  time.py              # timestamp helper

field/
  discover.py          # passive discovery and transactional bootstrap
  redirect.py          # validated temporary nftables redirect primitive
  README.md            # field workflows and lower-level operational details

systemd/
  ocpp-csms.service.in
  ocpp-discover.service.in

discover.sh            # run/install/uninstall optional OCPP Discover
install.sh             # base appliance installer
```

This README is the canonical project documentation. Important operator information belongs here; detailed field-harness procedures stay in `field/README.md` so the main path remains easy to scan.
