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
ocpp-csms config charger-01
ocpp-csms config charger-01 HeartbeatInterval
ocpp-csms profile list
ocpp-csms profile help max-power
ocpp-csms profile set max-power --watts 60000
ocpp-csms profile composite
ocpp-csms profile clear
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

`init` creates the SQLite database and transaction archive. `status` is read-only. `transactions` is the canonical read-only transaction inspector and `txn` is its exact alias. It never starts, stops, closes, repairs, or deletes a transaction. `config` performs a live OCPP `GetConfiguration` query against a connected charger. `profile` exposes the deliberately small Smart Charging surface described below. `events` reads the recorded OCPP/runtime timeline. `explain` presents the same evidence for one charger and incident window; it does not infer a root cause.

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
ocpp-csms config charger-01
ocpp-csms config charger-01 SupportedFeatureProfiles GetConfigurationMaxKeys
ocpp-csms config charger-01 HeartbeatInterval MeterValueSampleInterval
ocpp-csms config charger-01 HeartbeatInterval --force
ocpp-csms profile list
ocpp-csms profile help max-power
ocpp-csms profile set max-power --watts 60000
ocpp-csms profile composite
ocpp-csms profile composite --cp 1 --duration 7200
ocpp-csms profile composite --json
ocpp-csms profile clear
ocpp-csms profile clear --purpose ChargePointMaxProfile
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
- successful `config` queries return exit status `0`;
- OCPP `Rejected`, disconnected chargers, blocked configuration queries, and control-socket failures return exit status `1`;
- invalid command-line arguments use normal `argparse` behavior and return exit status `2`.

## Smart Charging profiles

Smart Charging is intentionally **stateless on the CSMS side**. The CSMS does not keep a desired profile inventory or claim that a previously sent profile is still installed. The charger owns its Smart Charging state and is the source of truth. Site/business policy such as "this location is capped at 60 kW" belongs in an upper layer that can call these commands when it wants to enforce that policy.

The CLI exposes built-in profile templates rather than requiring operators to construct full OCPP charging-profile payloads by hand:

```bash
ocpp-csms profile list
ocpp-csms profile help max-power
```

The initial and currently only built-in template is `max-power`. It creates a station-wide OCPP `ChargePointMaxProfile` on connector `0`, with a stable charging profile ID and stack level and an absolute schedule in watts:

```text
SetChargingProfile
  connectorId: 0
  csChargingProfiles:
    chargingProfileId: 1
    stackLevel: 0
    chargingProfilePurpose: ChargePointMaxProfile
    chargingProfileKind: Absolute
    chargingSchedule:
      chargingRateUnit: W
      chargingSchedulePeriod:
        - startPeriod: 0
          limit: [watts]
```

Apply it with a positive watt value:

```bash
ocpp-csms profile set max-power --watts 60000
```

If exactly one charger is connected, it is inferred. Use `--charger CHARGER` when an explicit target is needed. The command sends `SetChargingProfile` immediately and reports the charger's `Accepted` or `Rejected` result. Nothing is stored locally as desired Smart Charging state. Reapplying `max-power` uses the same OCPP charging profile ID (`1`) rather than inventing a locally named profile instance.

OCPP 1.6 does not provide a general request for enumerating every installed charging profile. `profile composite` therefore uses `GetCompositeSchedule` to ask the charger for the **effective schedule** it currently computes:

```bash
ocpp-csms profile composite
ocpp-csms profile composite --cp 1 --duration 7200
ocpp-csms profile composite --json
```

The defaults are connector `0` and a 3600-second query window. Human-readable output shows the schedule start, rate unit, and every returned period. `--json` prints the structured charger response for higher-level tooling. A composite schedule is an effective result, not a reconstruction of the individual profiles that produced it.

Some chargers accept station-wide profiles on connector `0` but reject `GetCompositeSchedule` for connector `0`. In that case the CSMS first preserves the standards-defined connector-0 attempt, then automatically queries every known nonzero physical connector with the same duration and rate unit. A fully successful fan-out still exits `0` and the human output clearly reports that aggregate connector-0 inspection is incompatible before showing each physical connector schedule. JSON reports `compatibility_fallback: "physical_connectors"` and preserves every individual response rather than fabricating a station-level aggregate. If any physical connector query fails, the fallback remains non-successful and the per-connector evidence is still shown.

`profile clear` sends OCPP `ClearChargingProfile`. With no filters it asks the charger to clear every profile the charger permits to be cleared:

```bash
ocpp-csms profile clear
```

Optional OCPP filters can target a profile ID, connector, purpose, or stack level:

```bash
ocpp-csms profile clear --id 7
ocpp-csms profile clear --cp 1
ocpp-csms profile clear --purpose ChargePointMaxProfile
ocpp-csms profile clear --stack-level 2
```

The same single-connected-charger inference applies to `set`, `composite`, and `clear`; each accepts `--charger CHARGER` when an explicit target is required.

A simple stateless reconciliation workflow is therefore:

```bash
ocpp-csms profile composite
ocpp-csms profile clear
ocpp-csms profile set max-power --watts 60000
ocpp-csms profile composite
```

The first query observes current effective behavior. Clearing removes the underlying clearable profiles rather than a "composite schedule" object. The new template is then applied, and the final composite query verifies what the charger reports afterward.

`SetChargingProfile`, `ClearChargingProfile`, and `GetCompositeSchedule` use the same live-session control path and OCPP evidence recording as the other outbound charger commands. They are not guarded merely because a transaction is active: charging profiles are explicitly intended to affect active or future charging behavior.

## GetConfiguration safety policy

`config` is deliberately read-only. It sends OCPP 1.6J `GetConfiguration` and displays exactly what the charger reports; the CSMS does not coerce values, infer defaults, cache a configuration model, or change charger settings.

With no keys, the command asks the charger for its available configuration:

```bash
ocpp-csms config charger-01
```

Specific keys can be requested positionally:

```bash
ocpp-csms config charger-01 HeartbeatInterval MeterValueSampleInterval
```

Some chargers observed in the field behave unreliably when configuration traffic is interleaved with transaction traffic. Because of that operational experience, the CSMS blocks `GetConfiguration` by default whenever the requested charger has an active transaction. This is a defensive appliance policy, not an OCPP protocol requirement.

The active check uses the same transaction-query model as `txn --active` and `status --charging`. An active transaction on another charger does not block the request.

When an operator deliberately needs to query a charger during an active transaction, `-f` and `--force` bypass the guard:

```bash
ocpp-csms config charger-01 HeartbeatInterval -f
ocpp-csms config charger-01 --force
```

A forced request is sent immediately; there is no second confirmation prompt. Use the override only when the risk of interleaving configuration traffic with live transaction traffic is understood.

The charger response preserves both recognized configuration entries and any `unknownKey` values. A reported key's `readonly` state is displayed as read-only or read/write, while its value remains the charger-provided string.

Evidence remains explicit. A blocked request does not create a fake OCPP event because no OCPP frame was sent. Instead, the runtime evidence records `configuration_query_blocked` with the active transaction IDs. A forced bypass during an active transaction records `configuration_query_forced`. When a request is actually sent, the event stream records the outbound and inbound `GetConfiguration` frames normally.

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

- `config` -> `GetConfiguration`;
- `profile set` -> `SetChargingProfile`;
- `profile composite` -> `GetCompositeSchedule`;
- `profile clear` -> `ClearChargingProfile`;
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

Incoming OCPP requests and handled replies are recorded together with CSMS-initiated remote requests and charger confirmations. Connection lifecycle, guarded-configuration decisions, Smart Charging requests and confirmations, and recovery diagnostics are also preserved. Unresolved transaction collisions are stored outside the normal transaction archive so they cannot silently mutate an unrelated transaction.

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

Remote-control tests cover outbound OCPP payloads, live-session replacement, Unix-socket dispatch, CLI request/exit behavior, and the key state invariant: an accepted remote start or stop command does not itself create or close a transaction. GetConfiguration tests additionally cover all-key and selected-key requests, the active-transaction guard, forced bypass, per-charger isolation, structured responses, and guard-decision evidence. Smart Charging tests cover the protocol transport, template mapping, composite rendering, clear filters, and an in-process CLI-to-control set -> composite -> clear workflow. Separate evidence tests verify that requests and confirmations are preserved with the correct `out`/`in` direction.

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
systemd/
  ocpp-csms.service.in
```

This README is the project documentation. For a project this small, important operational information should remain here rather than being split across a separate documentation tree.