# OCPP CSMS

A deliberately small Python OCPP 1.6J CSMS intended to run as an appliance-style service.

Its default policy is simple: **accept chargers, avoid blocking charging, preserve evidence, and expose a small diagnostic and control surface.** The implementation stays intentionally direct so operational behavior remains easy to inspect.

## Quick start

From a repository checkout:

```bash
sh install.sh
```

Do not run the installer itself with `sudo`. The installed `ocpp-csms.service` runs as the installing user, listens on `0.0.0.0:9000` by default, starts at boot, and restarts on failure.

Check the appliance with:

```bash
ocpp-csms status
sudo systemctl status ocpp-csms
sudo journalctl -u ocpp-csms -f
```

If the charger is already configured for this CSMS, nothing else is required.

Before replacing an existing appliance, installation performs a read-only usage preflight. Active charging always blocks replacement. Connected but idle chargers require explicit handoff authorization:

```bash
sh install.sh --rollover
```

`--rollover` never overrides active charging. The installer stages the replacement, repeats the charging check immediately before cutover, and requires fresh reconnect evidence from chargers connected at the final gate.

## OCPP Discover

Some chargers retain an old CSMS endpoint that cannot easily be reconfigured. OCPP Discover is the companion service for those installations.

Install it with the appliance:

```bash
sh install.sh --with-discover
```

or manage it directly:

```bash
./discover.sh                         # run discovery now
./discover.sh --interface eno1       # select charger-facing interface
./discover.sh --install              # install and enable at boot
./discover.sh --cleanup              # remove transient Discover-owned state
./discover.sh --uninstall            # remove Discover and its owned state
```

The installed Python command is also available as `ocpp-discover`. `ocpp-discover run ...` performs an explicit discovery attempt; `ocpp-discover service ...` is the boot observation and reconciliation path used by systemd.

### Discover operating model

Discover separates three kinds of truth:

```text
expected     /var/lib/ocpp-discover/discovered.json
configured   /etc/ocpp-discover/nftables.conf
observed     live charger traffic and current-process CSMS evidence
```

`discovered.json` records **what adaptation was proven**. It does not contain runtime health such as `healthy`, `offline`, or `last_status`.

The nftables fragment is executable persistent host configuration. Installation adds a managed include to `/etc/nftables.conf`; Debian loads the fragment during normal boot. Discover does not replay the adaptation itself and does not restart or replace the global nftables ruleset.

A normal boot is deliberately optimistic:

1. Debian loads the previously proven Discover fragment.
2. The CSMS starts normally.
3. Discover reads and validates `discovered.json`.
4. Fresh current-process connection evidence plus fresh inbound OCPP proves the adaptation for this boot.
5. Discover exits successfully without rewriting networking or persistent state.

If the expected charger is not observed during the startup window, Discover emits one clear error and enters passive waiting. **Charger absence is evidence to investigate, never permission to mutate networking.** It does not repeatedly rediscover, rewrite nftables, manipulate addresses, or restart services merely because the charger is offline.

If charger activity appears later, Discover validates it again. A normal late connection still requires fresh inbound OCPP. A positively observed different plaintext WebSocket endpoint can enter safe reconciliation.

Reconciliation is intentionally narrow:

1. observe positive contradictory endpoint evidence;
2. refuse reconciliation while any transaction is active;
3. replace only Discover's owned live nftables table with the candidate;
4. require fresh reconnect and fresh inbound OCPP;
5. only after proof, replace the persistent fragment and `discovered.json`;
6. if proof fails, restore the previously proven live and durable adaptation.

Configuration mismatch by itself does not authorize reconciliation. Discover waits for charger evidence rather than guessing.

### First discovery

When no proven adaptation exists, Discover first looks for validated plaintext HTTP WebSocket Upgrade traffic. If the charger is already targeting an IPv4 address owned by the host, Discover can install only the narrow source/destination/port redirect and does not claim another address.

If no usable host-local endpoint is visible, the discovery path can fall back to unresolved-ARP evidence, temporarily claim only the exact required IPv4 address as an additive `/32`, observe the charger endpoint, and install the narrow redirect. Temporary state is rolled back on failure. Only a candidate proven by fresh CSMS connection and inbound OCPP is promoted to persistent adaptation.

TLS/WSS traffic is opaque to this mechanism and is refused rather than guessed.

### Discover dependencies and ownership

`discover.sh --install` installs missing Debian runtime dependencies when necessary:

```text
tcpdump
nftables
iproute2
```

A plain discovery run never installs packages automatically.

Discover owns only:

```text
/etc/ocpp-discover/nftables.conf
/var/lib/ocpp-discover/discovered.json
/run/ocpp-discover/
/etc/systemd/system/ocpp-discover.service
```

Install/upgrade is the normal place where the managed `/etc/nftables.conf` include is changed. Runtime discovery never edits that global file. Uninstall removes only Discover-owned integration and state and does not remove packages that may be used elsewhere.

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

**Do not expose the built-in listener directly to an untrusted network or the public Internet.** Use an appropriate private network, VPN, firewall, or TLS-terminating boundary when chargers must cross a broader network.

OCPP Discover does not change this trust model. It is a local bootstrap mechanism for a charger-facing Ethernet network.

Operator commands use `<data-dir>/control.sock`, a Unix-domain socket created mode `0660`; they do not expose a second TCP control service.

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

All commands accept `--data-dir PATH` before the command name. `--cp` is the lowercase alias for `--connector`; `--txn` is the lowercase alias for `--transaction`. Diagnostic timestamps accept ISO-8601 and treat timestamps without an offset as UTC.

`status` is read-only. `transactions` is the canonical transaction inspector and `txn` is its exact alias. `events` reads recorded evidence; `explain` presents evidence for one charger and incident window without inventing a root cause.

Control-command exit behavior is simple: success returns `0`, OCPP rejection/disconnection/guard/control-socket failure returns `1`, and invalid arguments use normal `argparse` behavior and return `2`.

## Transactions and remote control

`txn --active` shows unfinished transactions and agrees with `status --charging`. `txn --last` shows the newest matching completed transaction. A positional transaction ID opens detail; `--events` adds its OCPP timeline.

Remote control stays inside the daemon that owns the live charger WebSocket. Supported commands include `GetConfiguration`, Smart Charging profile operations, remote start/stop, and Soft/Hard reset. Commands are sent only to chargers connected to the current process and are never queued for later delivery.

An accepted remote command and the resulting charger state are separate facts. For example, an accepted `RemoteStartTransaction` does not create a transaction until the charger sends `StartTransaction`.

`config` is blocked by default for a charger with an active transaction because some field chargers behave unreliably when configuration traffic is interleaved with charging. `--force` deliberately bypasses that appliance safety guard.

Smart Charging remains stateless on the CSMS side. The charger owns installed profiles and effective schedules; upper layers own business/site policy.

## Transaction recovery

Queued OCPP traffic can outlive the CSMS instance that issued a transaction ID. Recovery preserves evidence without silently merging unrelated activity. Historical IDs can be adopted when free, recovered IDs do not advance the local sequence, unknown historical stops create recovered stopped records, and collisions with unrelated local transactions remain unresolved rather than overwriting either transaction.

## Data and evidence

By default:

```text
~/ocpp-csms-data/
  control.sock
  ocpp-csms.sqlite3
  ocpp-csms.sqlite3.schema-<old-version>.bak
  transactions/
  transactions-unresolved/
```

SQLite stores append-oriented OCPP evidence, runtime events, transaction state, connector status, and diagnostic state. JSON transaction archives remain directly readable and copyable.

Schema versions are independent of application releases. New databases are created at the current schema; normal CSMS startup never upgrades an old database implicitly. Installation performs explicit compatibility inspection and creates a SQLite-safe backup before a supported upgrade. Unknown, unversioned, or newer schemas are refused rather than guessed.

## Installed layout

The normal appliance runs as the installing user. The base installer uses `sudo` only for system installation/management.

```text
~/.local/share/ocpp-csms/venv/
~/.local/share/ocpp-csms/venv.previous/
~/.local/share/ocpp-csms/venv.next/
~/.local/bin/ocpp-csms
~/.local/bin/csms
~/ocpp-csms-data/
/etc/systemd/system/ocpp-csms.service

/etc/ocpp-discover/nftables.conf
/var/lib/ocpp-discover/discovered.json
/run/ocpp-discover/
/etc/systemd/system/ocpp-discover.service
```

`venv.next` is staging. `venv.previous` is retained after promotion so startup failures can restore the previous binary environment when the database schema has not changed.

## Development and tests

Install development dependencies with:

```bash
python -m pip install -e ".[dev]"
```

The suites are intentionally separated:

```bash
python -m pytest tests --ignore=tests/ocpp_discover
python -m pytest tests/ocpp_discover
python -m pytest field/tests
```

CI runs all three boundaries on Debian 12 / Python 3.11 as the primary appliance target and Debian 13 / Python 3.13 as the compatibility target.

Tests favor behavioral and state invariants over exact human-readable output. Discover tests cover ARP and TCP/WebSocket evidence, ownership, narrow redirects, persistence, boot observation, passive offline waiting, positive contradiction, active-charge refusal, proof-before-persist, and rollback.

## Source layout

```text
src/
  ocpp_csms/          # OCPP server, evidence, status, diagnostics, transactions and control
  ocpp_discover/
    discover.py       # network evidence and first discovery
    redirect.py       # narrow nftables adaptation
    handoff.py        # proof and promotion of a discovered adaptation
    persistence.py    # owned persistent nftables integration
    service.py        # boot observation and passive waiting
    diagnosis.py      # compare expected/configured/observed state
    reconcile.py      # prove and replace a contradicted adaptation
    lifecycle.py      # install/uninstall owned persistent integration

field/                # field harness and compatibility helpers
systemd/              # appliance service templates
discover.sh            # Discover run/install/cleanup/uninstall helper
install.sh             # staged appliance installer
```

This README is the canonical project documentation. Field-only procedures belong in `field/README.md`; production behavior belongs here.
