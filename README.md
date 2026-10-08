# OCPP CSMS

A deliberately small Python OCPP 1.6J CSMS intended to run as an appliance-style service.

Its default policy is simple: **accept chargers, avoid blocking charging, preserve evidence, and expose a small diagnostic and control surface.**

## CSMS operating model

The CSMS is permissive by default:

- unknown charge-point identities are accepted;
- RFID authorization is accepted by default when no `rfid.csv` authorization file is present;
- a charger is not rejected solely for an unexpected/missing `ocpp1.6` subprotocol negotiation;
- anomalies are recorded as evidence instead of automatically blocking charging.

The design principle is: **observe aggressively; block reluctantly.**

## Commands

Run `ocpp-csms --help` for the authoritative current surface.

Common operations include:

```text
ocpp-csms status [CHARGER]
ocpp-csms status --charging
ocpp-csms chargers [--charging] [--json]
ocpp-csms cps ...
ocpp-csms charger CHARGE_POINT [--json]
ocpp-csms cp CHARGE_POINT [--json]
ocpp-csms transactions [ID] [--active|--last] [--cp CHARGE_POINT|--charger CHARGE_POINT] [-c N|--connector N] [--events]
ocpp-csms txn ...
ocpp-csms rfid report [RFID]
ocpp-csms rfid export [CHARGER]
ocpp-csms rfid version [CHARGER]
ocpp-csms rfid clear [CHARGER]
ocpp-csms config [KEY ...] [--cp CHARGE_POINT|--charger CHARGE_POINT] [-f|--force]
ocpp-csms config download [CHARGER] [-f|--force] [--show-sensitive] [--json] [--output FILE]
ocpp-csms profile templates
ocpp-csms profile help TEMPLATE
ocpp-csms profile send max-power --watts WATTS [--start ISO-8601] [--cp CHARGE_POINT|--charger CHARGE_POINT]
ocpp-csms profile composite [--cp CHARGE_POINT|--charger CHARGE_POINT] [-c N|--connector N] [--duration SECONDS] [-T|--local-time] [--json]
ocpp-csms profile clear ...
ocpp-csms start CHARGER [-c N|--connector N] --id-tag TAG (--now|--after SECONDS|--within SECONDS)
ocpp-csms stop CHARGER (--transaction ID|--txn ID) (--now|--after SECONDS|--within SECONDS)
ocpp-csms reset CHARGER [--hard] (--now|--after SECONDS|--within SECONDS)
ocpp-csms events [CHARGER] [--since TIME] [--until TIME] [--limit N]
ocpp-csms explain CHARGER --at TIME [--minutes N]
```

### Status and charger views

`charger`/`cp` show one charge point and its connector detail; `chargers`/`cps` list known charge points. `-c` aliases `--connector`. `--cp` selects a charge point, with `--charger` retained as an equivalent long-form alias.

### Transactions and events

`transactions`/`txn` expose transaction history and active/last transaction views. `--txn` aliases `--transaction` where a transaction ID is accepted. `events` exposes recorded runtime and OCPP evidence, while `explain` provides a time-centered diagnostic view for a charger.

### Configuration

`config download` issues a full OCPP `GetConfiguration` and records every returned key with both its `readonly` flag and current value.

When exactly one charger is connected, the charger ID may be omitted. With multiple connected chargers, an explicit charger is required.

Full configuration queries are blocked while the selected charger has an active transaction. This keeps nonessential OCPP traffic out of the active-charging path. `-f` / `--force` deliberately bypasses that guard.

Sensitive values are masked by default using `[REDACTED]`. Use `--show-sensitive` only when the actual secret-bearing values are intentionally required.

Examples:

```bash
ocpp-csms config download
ocpp-csms config download CHARGER
ocpp-csms config download CHARGER --json
ocpp-csms config download CHARGER --output charger-config.json
ocpp-csms config download CHARGER --show-sensitive --output charger-config-private.json
ocpp-csms config download CHARGER --force
```

`--output` writes a structured JSON snapshot. Human-readable terminal output remains available unless `--json` is requested.

### Remote start, stop, and reset

Remote commands are sent only to chargers connected to the current CSMS process and are never queued for later delivery. Accepted commands and resulting charger behavior are recorded as separate facts.

Remote start, stop, and reset commands require one timing mode:

- `--now` attempts the command immediately.
- `--after SECONDS` waits exactly that long before evaluating execution conditions and attempting the command.
- `--within SECONDS` attempts immediately when unblocked; if an active transaction blocks a start or reset, it waits up to that many seconds for charging to stop before proceeding.
- Remote stop is not blocked by an active transaction, so `--within` behaves like immediate execution for stop while `--after` still delays it.

Start and reset are rejected while the selected charger has an active transaction. For `--after`, that check is intentionally made only after the delay expires. These waits live in the running CSMS control service; they do not turn disconnected chargers into queued targets.

### Smart Charging

The built-in `max-power` template is an Absolute `ChargePointMaxProfile` anchored by default at `2000-01-01T00:00:00Z`. The deliberately old fixed start avoids making immediate station-wide limits depend on close agreement between charger and CSMS clocks. Use `--start` with an ISO-8601 date-time including timezone to override that anchor; explicit values are normalized to UTC before being sent.

`profile composite -T` converts the returned `scheduleStart` to the CSMS host local timezone for human-readable output. JSON output remains unchanged and preserves the charger/OCPP timestamp exactly as returned.\n\nSmart Charging is intentionally stateless on the CSMS side. The charger owns installed profiles and effective schedules; upper layers own site/business policy.

## RFID authorization

RFID authorization is optional and file-backed. With no
`<data-dir>/rfid.csv`, every RFID remains accepted. When the file exists it
becomes the allow list and is re-read for every authorization decision, so
editing it does not require restarting the service.

The smallest valid file is one RFID per line:

```text
CARD-A
CARD-B
```

An optional header makes richer CSV files self-describing and allows columns to
be reordered:

```csv
rfid,name,enabled
CARD-A,Alice,true
CARD-B,Former employee,false
```

Without a header, extended rows use the fixed order
`rfid[,name[,enabled]]`. With a header, supported columns are `rfid`,
`name`, and `enabled`; `rfid` is required. Blank lines and lines beginning
with `#` are ignored. Duplicate RFIDs or malformed values make the file
invalid.

When `rfid.csv` is present, an enabled listed RFID is `Accepted`, an
unlisted RFID is `Invalid`, and a listed disabled RFID is `Blocked`. An
invalid authorization file fails closed as `Blocked`. The same policy is
applied to both OCPP `Authorize` and `StartTransaction`.

Human-readable `ocpp-csms status` reports one of:

```text
RFID authorization: Allow All
RFID authorization: rfid.csv (27 cards)
RFID authorization: rfid.csv (invalid)
```

Machine-readable status uses `null` when no RFID authorization file is
configured and a structured object when one is present.

`ocpp-csms rfid report` summarizes every RFID observed in transaction history,
including transaction count and total energy. The authorization columns adapt to
the sources that are actually known at report time:

- with `rfid.csv` only, `ALLOW` and `NAME` describe the current file;
- with no `rfid.csv` but a connected charger whose current local-list version
  matches accepted CSMS history, `ALLOW` and `NAME` describe that charger
  cache;
- with both a file and a known connected charger cache, `ALLOW` describes the
  file and `CACHE` describes the charger;
- an observed RFID absent from a known source shows `missing`; an unrecognized
  live charger-list version shows `unknown` for `CACHE`;
- when the charger is disconnected, or when the CSMS has never successfully sent
  it a list, charger cache columns are omitted rather than inferred from stale
  history.

When a connected charger has accepted-list history, the report also shows
`Charger local list: version N`. If `rfid.csv` is present, a `Sync:` line reports
`current`, `differs`, or `unknown` by comparing the current enabled-card
set with the stored snapshot for the charger's live list version. Supplying a
tag keeps the detailed per-RFID report:

```bash
ocpp-csms rfid report
ocpp-csms rfid report CARD-A
```

The same `rfid.csv` can be exported to a connected OCPP 1.6 charge point's
Local Authorization List:

```bash
ocpp-csms rfid export [CHARGER]
ocpp-csms rfid version [CHARGER]
ocpp-csms rfid clear [CHARGER]
```

`export` sends only entries whose `enabled` value is true, using a Full
`SendLocalList` update. The CSMS first reads `GetLocalListVersion`, chooses a
new version that does not reuse any version previously accepted for that
charger, sends the list, and then reads the charger version again for
verification. `clear` sends an empty Full list at version 0 and verifies that
the charger reports version 0. These operations require charger support for the
optional OCPP 1.6 Local Authorization List Management feature.

Accepted lists are stored in SQLite by charger and list version, together with
the exact RFID/name snapshot that was sent, its hash, and the version reported
by the charger after the update. Rejected or failed `SendLocalList` attempts
are not stored in the RFID-list history; their OCPP request/response evidence
remains in the normal event log. Standard OCPP 1.6 exposes the current list
version but does not provide an operation to download the charger's list
contents, so the CSMS keeps this accepted-list history itself.

## Runtime architecture

The WebSocket listener uses a direct event loop. Normal OCPP handling persists evidence after each handler completes; there is no Django, Celery worker, async queue, or desired-state engine behind the protocol path.

The listener uses plain `ws://` and is intended for a trusted charger LAN or equivalent private boundary. Do not expose it directly to an untrusted/public network.

Operator control uses `<data-dir>/control.sock`, a Unix-domain socket, rather than a second TCP control service.

## Data and evidence

Default CSMS data:

```text
~/ocpp-csms-data/
  control.sock
  ocpp-csms.sqlite3
  ocpp-csms.sqlite3.schema-<old-version>.bak
  transactions/
  transactions-unresolved/
```

SQLite stores append-oriented OCPP evidence, runtime events, transaction state, connector status, and diagnostic state. JSON transaction archives remain directly readable.

Schema versions are independent from application releases. Startup does not silently upgrade an old database. Deployment performs explicit compatibility inspection and creates a SQLite-safe backup before a supported upgrade.

## Quick start

The canonical appliance deployment is Ansible:

```bash
./deploy.sh
```

Do not run the wrapper itself with `sudo`; the playbook uses privilege escalation only for host-level integration.

The deployment wrapper exposes a small operator surface before any raw
Ansible arguments:

```bash
./deploy.sh --observe 180
./deploy.sh --interface eno1
./deploy.sh --reconnect 60
./deploy.sh --stage-only
./deploy.sh --diagnose
```

`--observe SECONDS` changes the bounded incumbent-traffic observation window
(default 15 seconds). `--interface NAME` selects the charger-facing interface
for both Discover and incumbent observation. `--reconnect SECONDS` changes the
post-cutover charger reconnect timeout. `--stage-only` installs and validates
the candidate, runs safety and incumbent inspection, reports the planned handoff,
then exits before stopping any service. `--diagnose` runs a separate read-only
playbook that reports safety preflight, observed incumbent ownership, current
managed release/service state, and Discover diagnostics without staging or
handoff.

Wrapper options must appear before raw Ansible arguments. `--diagnose` may be
combined with `--observe` and `--interface`, but not with `--stage-only` or
`--reconnect`.

The satellite playbook installs and manages both:

```text
ocpp-csms.service
ocpp-discover.service
```

Check the appliance with:

```bash
ocpp-csms status
ocpp-discover status
sudo ocpp-discover diagnostics
sudo systemctl status ocpp-csms ocpp-discover
```

If the charger-facing interface is not `eth0`, prefer the wrapper form
`./deploy.sh --interface eno1`. Raw Ansible `-e` arguments remain
available for advanced overrides.

## Appliance handoff and rollback

Installation performs read-only safety checks before replacement. Active charging blocks replacement.

For an existing appliance, the candidate release is installed into an immutable release directory and started before `current` is promoted. Previously connected chargers must reconnect with fresh evidence before the release becomes active.

If a charger cannot reconnect because it still targets an old local endpoint, the handoff can temporarily run the **candidate release's** OCPP Discover implementation. That recovery must produce a valid persistent ownership receipt before deployment proceeds. If verification fails, the previous service/unit is restored when rollback is safe.

The active release is therefore the last release that completed handoff successfully; an in-progress candidate does not become `current` early.

## OCPP Discover

OCPP Discover is the resident network-adaptation companion for chargers whose configured plaintext OCPP endpoint cannot easily be changed.

The installed operator surface is:

```text
ocpp-discover status [--json]
ocpp-discover diagnostics [--json]
ocpp-discover run ...
ocpp-discover cleanup --state-dir /run/ocpp-discover
ocpp-discover service ...
```

`status` is cheap and read-only. `diagnostics` compares the proven endpoint with persistent and live nftables state and may require root because Discover's durable state is root-owned.

Normal appliance operation does not require manually running discovery. `service` is the resident systemd entry point.

### Discover truth model

Discover keeps three distinct kinds of truth:

```text
expected     /var/lib/ocpp-discover/discovered.json
configured   /etc/ocpp-discover/nftables.conf
observed     current charger traffic and CSMS evidence
```

`discovered.json` records the adaptation that was actually proven. It is ownership/evidence, not health state.

The persistent nftables fragment contains the exact narrow redirect that should exist after reboot. Debian loads that fragment normally; Discover does not replay the global ruleset or restart nftables at runtime.

### First discovery

When no proven adaptation exists, Discover waits for qualifying first-contact evidence.

If a fresh TCP SYN identifies a charger source and a **host-local** destination address/port that does not reach the CSMS listener, Discover can construct a provisional exact-match redirect from that tuple. The SYN alone does not authorize persistence: the redirect is temporary until a subsequent plaintext WebSocket/OCPP connection proves it.

If no usable TCP destination exists, discovery may fall back to repeated unresolved ARP evidence and temporarily claim only the required address as an additive `/32` while diagnosing the endpoint.

A successful candidate must be proven by fresh CSMS connection and fresh inbound OCPP before Discover writes both:

```text
/var/lib/ocpp-discover/discovered.json
/etc/ocpp-discover/nftables.conf
```

Temporary state is rolled back on failure. TLS/WSS traffic is opaque and is refused rather than guessed.

### Reconciliation

Once an adaptation is proven, charger absence never authorizes mutation. Discover waits passively for evidence.

Positive contradictory plaintext endpoint evidence may trigger reconciliation only when charging is inactive. Reconciliation changes only Discover-owned networking, requires fresh reconnect/OCPP proof, and restores the previous proven adaptation if proof fails.

### Discover ownership

Discover owns only:

```text
/etc/ocpp-discover/nftables.conf
/var/lib/ocpp-discover/discovered.json
/run/ocpp-discover/
/etc/systemd/system/ocpp-discover.service
```

Runtime discovery never edits the global `/etc/nftables.conf` file and never flushes or replaces unrelated nftables state.

Appliance installation provides the runtime tools Discover needs:

```text
tcpdump
nftables
iproute2
```

## TLS configuration (Chunk 2A)

TLS configuration is optional and remains disabled by default. Registration does not
start a WSS listener; that is planned for Chunk 2B. No deployment flag or
CSMS service restart is needed for these read-only/configuration operations.

```sh
sudo ocpp-csms tls config --cert /etc/ocpp-csms/tls/server.crt --key /etc/ocpp-csms/tls/server.key --hostname csms.example.com --port 9443
ocpp-csms tls status
sudo ocpp-csms tls check
```

The configuration is stored atomically at `/etc/ocpp-csms/tls.json` with
restrictive permissions. Certificates and private keys remain at operator-supplied
absolute paths; no keys are copied into immutable releases or printed by status.
The `check` command checks local certificate/key loadability, expiration,
DNS Subject Alternative Name matching, and port conflict with the default WS
port. It requires OpenSSL to inspect certificate extensions. This cannot
establish whether any particular charger trusts the certificate. Run checks
under the CSMS service account to verify effective readability. Future chunks
will supply listener management, enable/disable, and hot reload.

## Installed layout

Ansible uses immutable releases with stable commands:

```text
~/.local/share/ocpp-csms/releases/<revision>/
~/.local/share/ocpp-csms/current
~/.local/share/ocpp-csms/previous
/usr/local/bin/ocpp-csms
/usr/local/bin/ocpp-discover
```

Shared appliance state/integration:

```text
~/ocpp-csms-data/
/etc/systemd/system/ocpp-csms.service
/etc/systemd/system/ocpp-discover.service
/etc/ocpp-discover/nftables.conf
/var/lib/ocpp-discover/discovered.json
/run/ocpp-discover/
```

## Development and tests

Install development dependencies with:

```bash
python -m pip install -e ".[dev]"
```

The main test boundaries are:

```bash
python -m pytest tests/ansible
python -m pytest tests --ignore=tests/ocpp_discover --ignore=tests/ansible
python -m pytest tests/ocpp_discover
```

CI runs those suites on:

```text
Debian 12 / Python 3.11 / ARM64   primary appliance target
Debian 13 / Python 3.13 / ARM64   compatibility target
```

Tests favor behavioral/state invariants over human-readable strings, historical tombstones, and deprecated compatibility surfaces.

## Source layout

```text
src/
  ocpp_csms/          OCPP server, evidence, transactions and control
  ocpp_discover/      resident discovery, adaptation and reconciliation

ansible/              canonical appliance convergence
deploy.sh              canonical deployment wrapper
```

This README is the canonical project documentation.
