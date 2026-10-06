# OCPP CSMS

A deliberately small Python OCPP 1.6J CSMS intended to run as an appliance-style service.

Its default policy is simple: **accept chargers, avoid blocking charging, preserve evidence, and expose a small diagnostic and control surface.**

## Quick start

The canonical appliance deployment is Ansible:

```bash
./ansible-deploy.sh
```

Do not run the wrapper itself with `sudo`; the playbook uses privilege escalation only for host-level integration.

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

If the charger-facing interface is not `eth0`:

```bash
./ansible-deploy.sh -e ocpp_discover_interface=eno1
```

`install.sh` remains only as a transitional legacy installer. New appliance deployment and validation should use Ansible.

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

## CSMS operating model

The CSMS is permissive by default:

- unknown charge-point identities are accepted;
- RFID authorization is accepted by default;
- a charger is not rejected solely for an unexpected/missing `ocpp1.6` subprotocol negotiation;
- anomalies are recorded as evidence instead of automatically blocking charging.

The design principle is: **observe aggressively; block reluctantly.**

The WebSocket listener uses a direct event loop. Normal OCPP handling persists evidence after each handler completes; there is no Django, Celery worker, async queue, or desired-state engine behind the protocol path.

The listener uses plain `ws://` and is intended for a trusted charger LAN or equivalent private boundary. Do not expose it directly to an untrusted/public network.

Operator control uses `<data-dir>/control.sock`, a Unix-domain socket, rather than a second TCP control service.

## Commands

Run `ocpp-csms --help` for the authoritative current surface.

Common operations include:

```text
ocpp-csms status [CHARGER]
ocpp-csms status --charging
ocpp-csms transactions [ID] [--active|--last] [--charger CHARGER] [--connector N|--cp N] [--events]
ocpp-csms txn ...
ocpp-csms config CHARGER [KEY ...] [-f|--force]
ocpp-csms profile list
ocpp-csms profile help TEMPLATE
ocpp-csms profile set max-power --watts WATTS [--charger CHARGER]
ocpp-csms profile composite [--charger CHARGER] [--connector N|--cp N] [--duration SECONDS] [--json]
ocpp-csms profile clear ...
ocpp-csms start CHARGER [--connector N|--cp N] --id-tag TAG
ocpp-csms stop CHARGER (--transaction ID|--txn ID)
ocpp-csms reboot CHARGER [--hard]
ocpp-csms events [CHARGER] [--since TIME] [--until TIME] [--limit N]
ocpp-csms explain CHARGER --at TIME [--minutes N]
```

`--cp` aliases `--connector`; `--txn` aliases `--transaction`.

Remote commands are sent only to chargers connected to the current CSMS process and are never queued for later delivery. Accepted commands and resulting charger behavior are recorded as separate facts.

Smart Charging is intentionally stateless on the CSMS side. The charger owns installed profiles and effective schedules; upper layers own site/business policy.

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

The legacy installer may still create older mutable paths during the migration period, but they are not the canonical deployment model.

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
python -m pytest field/tests
```

CI runs those suites on:

```text
Debian 12 / Python 3.11 / ARM64   primary appliance target
Debian 13 / Python 3.13 / ARM64   compatibility target
```

Tests favor behavioral/state invariants over human-readable strings, historical tombstones, and deprecated compatibility surfaces.

The `field/` package is only for real-hardware harness behavior such as takeover, reboot/configuration validation, soak, watchdog, and rollback. Production discovery/adaptation lives exclusively in `src/ocpp_discover/`; Smart Charging diagnostics use the normal `ocpp-csms profile ...` surface.

## Source layout

```text
src/
  ocpp_csms/          OCPP server, evidence, transactions and control
  ocpp_discover/      resident discovery, adaptation and reconciliation

ansible/              canonical appliance convergence
field/                real-hardware validation harness
systemd/              legacy installer service templates
install.sh             transitional installer
ansible-deploy.sh      canonical deployment wrapper
```

This README is the canonical production documentation. Real-hardware harness procedures live in `field/README.md`.