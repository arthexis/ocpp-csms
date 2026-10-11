
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
ocpp-csms stop CHARGER (--transaction ID|--txn ID) [--now|--after SECONDS|--within SECONDS]
ocpp-csms txn start [-c N] [--rfid TAG] [--now|--after SECONDS|--within SECONDS]
ocpp-csms txn stop ID [--now|--after SECONDS|--within SECONDS]
ocpp-csms reset CHARGER [--hard] (--now|--after SECONDS|--within SECONDS)
ocpp-csms events [CHARGER] [--since TIME] [--until TIME] [-n VALUE|--limit VALUE]
ocpp-csms report [--since TIME] [--until TIME] [--cp CHARGE_POINT] [--json]
ocpp-csms mail send report [--since TIME] [--until TIME] [--cp CHARGE_POINT]
ocpp-csms alerts [CHARGER] [--since TIME] [--until TIME] [-n VALUE|--limit VALUE] [-N] [--json]
ocpp-csms explain CHARGER --at TIME [--minutes N]
```

### Field maintenance (OCPP 1.6J)

Use the existing Unix control socket to send one-shot maintenance requests to a connected charge point.
Omit `--cp` when only one charge point is connected; otherwise specify `--cp ID` (or `--charger ID`).

```bash
ocpp-csms trigger heartbeat
ocpp-csms trigger status -c 1
ocpp-csms trigger meter -c 1
ocpp-csms availability disable -c 2
ocpp-csms availability enable -c 2
ocpp-csms availability disable  # connector 0: whole charge point
ocpp-csms unlock -c 1
```

Trigger supports `boot`, `heartbeat`, `status`, `meter`, `diagnostics`, and `firmware` when the charge point supports them; `-c` applies to status and meter only. Availability returns `Accepted`, `Rejected`, or `Scheduled`; Scheduled means the charger has deferred the change (typically until an active transaction finishes). Unlock requires a physical connector ID greater than zero. All commands support `--json` for the raw OCPP response. A charger accepting a request does **not** prove the physical action completed; inspect subsequent events and connector status.

### Capability inspection and reconciliation

```bash
ocpp-csms capabilities [--cp CP001] [--json]
ocpp-csms reconcile [--cp CP001] [-c 1] [--json]
ocpp-csms rfid cache clear [--cp CP001]
```

`capabilities` fetches `SupportedFeatureProfiles` from a connected charge point, labeling features **Advertised**, **Not advertised**, or **Unknown**. Advertised features are not necessarily verified as operational. `reconcile` is a **read-only local snapshot** of stored connector notifications and open transaction archives; it does not interrogate the charger, assert physical state, infer missing StopTransaction frames, or modify transactions. Disconnected charge points are marked uncertain. `rfid cache clear` issues OCPP `ClearCache`, and does not change the charger local authorization list managed by `rfid clear`.

### Status and charger views

`charger`/`cp` show one charge point and its connector detail; `chargers`/`cps` list known charge points. `-c` aliases `--connector`. `--cp` selects a charge point, with `--charger` retained as an equivalent long-form alias.

### Transactions and events

`txn start` and `txn stop` are convenient aliases for the existing remote `start` and `stop` operations, with `--now` assumed unless `--after` or `--within` is supplied. `txn stop ID` accepts the transaction ID directly, and `txn stop --txn ID` also works. Existing `txn ID`, `txn --active`, and other history queries remain unchanged.


`transactions`/`txn` expose transaction history and active/last transaction views. `--txn` aliases `--transaction` where a transaction ID is accepted. `events` exposes recorded runtime and OCPP evidence, while `explain` provides a time-centered diagnostic view for a charger.

By default, `events` shows the latest **100 events**. `-n` and `--limit` are interchangeable: an integer caps the number of matching events, while a duration (`s`, `m`, `h`, `d`, or `w`, case-insensitive) returns **all** events in that time window without a count cap. `--since` and `--until` accept either relative durations such as `3d` or ISO-8601 timestamps. A duration limit ends at `--until` when given, otherwise at the current UTC time; `--since` further restricts the window. The latest events are selected first and printed chronologically. Compact grouping and JSON output use the same filters.

```bash
ocpp-csms events                       # latest 100 events
ocpp-csms events -n 50                 # latest 50 events
ocpp-csms events -n 1d                 # all events from the last day
ocpp-csms events --since 3d            # latest 100 events within three days
ocpp-csms events --since 3d -n 1d      # all events within the last day
ocpp-csms events --until 2d -n 1d      # events from 3 to 2 days ago
ocpp-csms events -n 1.5h --json        # JSON events from the last 90 minutes
```

### Vendor DataTransfer (OCPP 1.6J)

Send opaque vendor-specific strings over the existing charge-point WebSocket:

```bash
ocpp-csms data-transfer send --cp CP001 --vendor com.example.vendor --message-id diagnostic --data '{"value":1}'
ocpp-csms data-transfer send --vendor com.example.vendor --data 'opaque text' --json
```

`--data` is transmitted as a string without interpreting or transforming its contents. The charger may respond with `Accepted`, `Rejected`, `UnknownVendorId`, or `UnknownMessageId`; only `Accepted` is considered successful. Incoming charger-originated DataTransfer messages are recorded in the existing evidence store and acknowledged with `UnknownVendorId` by default; they **never execute arbitrary vendor operations**. No vendor-specific plugins, new services, or schema changes are introduced.

### OCPP reservations

Use `reserve` to create an OCPP `ReserveNow` reservation; `reservation create` is an equivalent spelling. Reservation IDs are operator-supplied positive integers, unique for the charger. Connector 0 lets the charger choose any connector. The charger must support reservations; an `Accepted` response acknowledges the request, not guaranteed future availability.

```bash
ocpp-csms reserve --cp CP001 -c 1 --rfid ABC123 --id 42 --until 2026-10-12T18:00:00Z
ocpp-csms reservation create --cp CP001 -c 1 --rfid ABC123 --id 42 --until 1h
ocpp-csms reservation cancel 42 --cp CP001
```

Optional `--parent-rfid` maps to `parentIdTag`. Requests and responses use the existing OCPP evidence store. Reservation creation does not require a new service, scheduler, or local booking database. A reservation ID must be tracked by the operator for cancellation.

### Firmware management (OCPP 1.6J)

`UpdateFirmware` tells a connected charge point to retrieve a firmware image from a charger-reachable external URL. This may interrupt charging or reboot the charger. An explicit `--confirm` and one of `--now` or `--at` are required.

```bash
ocpp-csms firmware update --location https://example.org/fw.bin --now --confirm
ocpp-csms firmware update --cp CP001 --location https://example.org/fw.bin --at 2026-10-12T03:00:00Z --confirm
ocpp-csms firmware status --cp CP001
ocpp-csms firmware history --cp CP001 --since 7d
```

The CLI also supports `--retries`, `--retry-interval`, and `--json`. The OCPP `UpdateFirmware.conf` is an empty acknowledgement and **does not establish successful downloading or installation**. `firmware status` and `firmware history` inspect recorded `FirmwareStatusNotification` observations; no polling service or firmware hosting is installed. Firmware URLs are redacted in event evidence, including user info, path, and query tokens. Validate vendor/model compatibility and the charger's supported retrieval protocol before submitting an update.

### Charger diagnostic upload requests

`ocpp-csms diagnostics request --location URL` sends OCPP 1.6 `GetDiagnostics` to a connected charge point. The explicit URL must be reachable by the **charger**, and uploads are handled by an external server in this first version.

```bash
ocpp-csms diagnostics request --location https://uploads.example.org/charger
ocpp-csms diagnostics request --cp CP001 --location https://uploads.example.org/charger --since 1d --until now --retries 2 --retry-interval 30
ocpp-csms diagnostics history --cp CP001 --limit 100
ocpp-csms diagnostics history --json
```

Request evidence redacts upload credentials, path, and query strings. The `history` command reads the existing event store for GetDiagnostics requests/responses and DiagnosticsStatusNotification events. It does **not** establish strict request-to-notification correlation because the persisted event schema does not include OCPP message IDs, and an `Uploaded` notification is not proof that the destination received the file. A future optional CSMS-managed diagnostic receiver, provisioned by Ansible, will supply default upload URLs and manage stored files; that receiver is intentionally outside this PR.

### Incoming maintenance notifications

The CSMS accepts and records OCPP 1.6 `DiagnosticsStatusNotification` and `FirmwareStatusNotification`, including unsolicited notifications. Their status transitions are available in `events`; `UploadFailed`, `DownloadFailed`, and `InstallationFailed` additionally appear in `alerts`. Ordinary progress is not an alert. These handlers only acknowledge and preserve evidence; they do not request diagnostics, host uploads, or initiate firmware updates.

### Alerts

`alerts` shows exceptional recorded events as individual occurrences, without
compact grouping. It shares the relative/ISO time and count/duration limit
semantics of `events`, including `-N/--no-limit`. The default selects the
latest 100 **matching alerts**, rather than the latest 100 unfiltered events.
Use `--cp` (or `--charger`), a positional charger, or `--txn` to restrict
the result; `--json` exposes the versioned alert contract.

```bash
ocpp-csms alerts --since 3d
ocpp-csms alerts --since 3d -N
ocpp-csms alerts --cp CP001 --txn 123 --json
```

Ordinary charging starts remain events, not alerts. Future mail configuration
will allow opting into transaction-start emails independently, disabled by default.
The current classifier reports explicitly recognized faults, authorization
rejections, and disconnects; state-derived offline thresholds and recovery
correlation are future work.

### Operational reports

`ocpp-csms report` summarizes transactions whose start was received in the selected
period (default: last day), with individual RFID, charge point, connector, state,
measured kWh, and duration when available. The **SUBTOTAL** row gives the
period's transaction count, completed/incomplete breakdown, measured energy,
and meter coverage. Missing measurements are not treated as zero. Alerts are
counted over the selected period, while charger health is explicitly a **current**
snapshot, not a historical reconstruction.

```bash
ocpp-csms report --since 1d
ocpp-csms report --since 7d --cp CP001 --json
ocpp-csms mail send report --since 1d
```

Manual email requires SMTP. Scheduled delivery is configured independently:

```toml
[mail.reports.daily]
enabled = true
time = "08:00"
timezone = "America/Monterrey"

[mail.reports.weekly]
enabled = true
weekday = "monday"
time = "08:00"
timezone = "America/Monterrey"
```

`ocpp-csms mail schedule run` checks for due completed local calendar periods,
queues each report once per recipient with a stable period identity, and invokes
the existing retrying mail outbox. Ansible installs a persistent systemd timer
running this check every 15 minutes. This avoids a heavyweight scheduler and
does not send anything while mail is disabled. Reports include transaction RFIDs
and the period subtotal; charger health is a generation-time snapshot.

### SMTP mail transport

Ansible installs a documented, fully disabled `/etc/ocpp-csms/mail.toml`
only when the file is absent; later deploys preserve operator changes. It
validates the existing TOML as the service user before configuring the mail
timer, but does not create password files. The report timer is installed but
**inactive by default**; set Ansible variable `ocpp_csms_mail_timer_enabled:
true` to activate it, after configuring SMTP and the desired report schedules.


SMTP is optional and disabled unless explicitly enabled in
`/etc/ocpp-csms/mail.toml`. The mail command accepts a custom path via
`--config` before the subcommand:

```bash
ocpp-csms mail status
ocpp-csms mail status --json
ocpp-csms mail --config ./mail.toml test
```

Example configuration:

```toml
[mail]
enabled = false
from = "csms@example.com"
to = ["administrator@example.com"]

[mail.smtp]
host = "smtp.example.com"
port = 587
starttls = true
username = "csms@example.com"
password_env = "OCPP_CSMS_SMTP_PASSWORD"
timeout = 10

[mail.alerts]
enabled = true
minimum_severity = "warning"

[mail.events.transaction_started]
enabled = false
```

Set the named environment variable in the operator's environment (or securely
via systemd credentials when integrating a service). Never commit credentials
to version control. SMTP uses validated TLS and has a bounded timeout.
`mail test` explicitly sends one test email; SMTP acceptance does not prove
inbox delivery. Automatic notifications run independently of charger message handling and use
a small durable SQLite outbox in the data directory. Enable [mail.alerts]
to send qualifying exceptional events, and opt into [mail.events.transaction_started]
or [mail.events.transaction_stopped] for ordinary transactions. Cooldown
applies only to repeated alerts, never to distinct transactions. The first
enabled worker cycle starts from the current evidence high-water mark to avoid
backfilling historical messages. Failed SMTP attempts retry with bounded
exponential backoff.

```bash
ocpp-csms mail history
ocpp-csms mail history --failed
ocpp-csms mail history --json
```

`mail status` includes notification delivery counts when the outbox exists.
Email processing never blocks OCPP handlers. Scheduled digest reports are
planned for later chunks.

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

### Passive TLS endpoint observation

`sudo ocpp-discover tls-observe --interface eth0 --seconds 15` captures a bounded
sample of TCP traffic and reports candidate TLS ClientHello endpoints as JSON.
For offline inspection use `ocpp-discover tls-observe --pcap capture.pcap`
(classic Ethernet pcap format). The observer extracts source/destination
IPv4 addresses and ports, plaintext SNI when present, ALPN, and offered TLS
versions. TCP segments and TLS handshake fragments across records are joined
before parsing. This is **observation-only**: no address claims, redirects,
TLS interception, or durable adaptation changes. TLS candidates are **not**
proof of WSS, OCPP, or a successful charger connection. Missing/encrypted SNI
cannot be recovered; IPv6, pcapng, and IP-fragment reassembly are not yet
supported.

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

### Optional WSS listener (Chunk 2B)

The existing `ocpp-csms.service` starts plaintext WS as before. If the persistent
`/etc/ocpp-csms/tls.json` configuration has `"enabled": true` and passes
certificate/key checks, the same process also starts WSS on the configured
port. Both listeners dispatch to the existing OCPP handler and share session
and transaction state. TLS readiness and bind failures are logged without
bringing down WS. `tls config` continues to leave TLS disabled by default;
`tls enable` and live activation arrive in Chunk 2C. For field use, do not
manually enable WSS until the service account can read the private key and
the certificate has been validated. A service restart is required to apply
changes in this chunk; hot reload arrives in 2C.

### Live TLS management (Chunk 2C)

With the CSMS running, `ocpp-csms tls enable` activates the configured WSS
listener immediately, while `ocpp-csms tls reload` validates new credentials
and updates the existing listener TLS context for new connections. Existing
WS and WSS sessions are not restarted. `ocpp-csms tls status` reports live
listener state when the local Unix control socket is reachable.

TLS management commands are authorized through Linux Unix-socket peer
credentials: root or the running CSMS process UID. Existing charger control
commands retain their prior behavior. Use `--data-dir` for a non-default
control socket. TLS configuration and certificate files must be readable by
the CSMS service account, not just the operator writing them.

`tls disable` stops new WSS connections while established sessions finish.
The runtime reports retired listener ports under `draining_ports` until their
connections close. A changed WSS port is applied make-before-break on `tls
reload`: the new listener must bind successfully before the previous listener
stops accepting connections. Existing sessions survive both operations.
Normal CSMS shutdown still closes all connections. A charger using a retired
port cannot reconnect until its endpoint configuration is updated.

### Persistent TLS provisioning (Chunk 2E)

Ansible creates `/etc/ocpp-csms` (0750) and `/etc/ocpp-csms/tls`
(0700), owned by the existing non-root CSMS service user. On upgrade it
normalizes a regular `tls.json` to that service user's ownership and mode
0600 **without editing its contents**. It never creates or overwrites
certificate files, keys, or enabled/disabled state. The configuration and
credentials remain independent of immutable releases and survive upgrades.

Run `ocpp-csms tls config` as the service user rather than root (or correct
the resulting ownership) so the non-root daemon can read `tls.json`. A
private key deployed outside the managed TLS directory must likewise be
readable by that user; the role deliberately does not change arbitrary
certificate/key permissions. A configured and enabled TLS listener must
pass `tls check` under the service identity before Ansible proceeds with
service handoff. WS-only installations need no TLS credentials.

TLS check confirms **local readiness**, not that a remote charger trusts
the certificate or that the WSS port is publicly reachable. A live WSS
charger handshake and real-device reconnect remain separate field tests.

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


## OCPP Forwarder

`ocpp-forwarder` is an independent satellite-side process that forwards the
stable `ocpp-csms/export/v1` contract to an OCPP Collector. It does not read
the CSMS SQLite database or import the CSMS persistence layer.

Example one-page manual run:

```bash
ocpp-forwarder \
  --satellite-id gway-004 \
  --collector-url https://ocpp-collector.arthexis.com \
  --token-file /etc/ocpp-forwarder/token \
  run --once
```

The default persistent state file is:

```
/var/lib/ocpp-forwarder/state.json
```

The cursor is advanced only after events, energy samples, transaction snapshots,
charger state, and satellite progress have all been accepted by the Collector.
Failures therefore result in safe at-least-once retries against idempotent
Collector keys.

If the CSMS `source_id` changes because the local datastore was replaced, the
Forwarder starts the new source epoch from cursor zero while retaining the old
epoch centrally.

The long-running mode drains backlog pages immediately and sleeps only once it
is caught up:

```bash
ocpp-forwarder --satellite-id gway-004 run
```

Local forwarding state can be inspected without contacting the Collector:

```bash
ocpp-forwarder --satellite-id gway-004 status
```

Deployment as `ocpp-forwarder.service` is intentionally handled in the next
implementation chunk so the Forwarder program can be tested independently of
satellite provisioning.


### Deploying OCPP Forwarder

OCPP Forwarder deployment is opt-in. Ordinary satellite deployment does not
install or enable the Forwarder role:

```bash
./deploy.sh
```

Enable it explicitly with `--forwarder` and provide its satellite identity and
Collector token through Ansible variables (normally from Vault/host vars):

```bash
./deploy.sh --forwarder \
  -e ocpp_forwarder_satellite_id=gway-004 \
  -e @forwarder-secrets.yml
```

where the secret variable is:

```yaml
ocpp_forwarder_token: "<collector JWT>"
```

The deployed service is `ocpp-forwarder.service`. It has no
`Requires=`, `PartOf=`, or `BindsTo=` relationship with
`ocpp-csms.service`.

Collector reachability is not an Ansible deployment gate. If DNS, Internet, or
the Collector is unavailable, the Forwarder remains isolated from the CSMS,
keeps its cursor unchanged, logs a warning, and retries with exponential
backoff capped at 60 seconds. Normal charging and OCPP-CSMS operation continue
unaffected.

### Inspect (read-only charger investigation)

```bash
ocpp-csms inspect --cp CP001
ocpp-csms inspect --cp CP001 --deep
ocpp-csms inspect --cp CP001 --offline --json
ocpp-csms inspect --all
```

`status` reports existing CSMS observations without interrogating chargers. `inspect` compares stored connector/transaction observations and, by default, queries the selected connected charger for its advertised feature profiles. `--deep` additionally requests local RFID list version and a composite schedule. `--offline` performs **no** OCPP requests. Each live query has a bounded timeout (`--timeout SECONDS`) and errors are reported as unknown instead of aborting other checks. Neither command changes configuration, clears RFID caches, resets chargers, starts/stops charging, or attempts repair. Conflicts are reported as warnings; missing evidence and unsupported functionality are not automatic failures.

### Follow live OCPP events

Use `ocpp-csms events --follow` (or `events -f`) to print the initial filtered history and then stream newly persisted OCPP and runtime records until Ctrl+C. This observes the existing SQLite evidence store; it does not contact chargers, restart commands, or run a service.

```bash
ocpp-csms events -f
ocpp-csms events -f CP001 --since 10m
ocpp-csms events -f --txn 42
ocpp-csms events -f --json
```

Follow mode honors existing time-window, transaction, charger and history-limit filters. With `--json`, it outputs **one JSON event per line (JSONL)** instead of the one-shot events envelope, for straightforward streaming pipelines. It tracks OCPP and runtime event IDs separately to avoid relying on globally unique IDs. Routine events are displayed using the existing human-readable formatter, with no synthetic OCPP message correlation.

### Compare charger configuration snapshots

`config diff` is read-only and uses the same JSON snapshot format as `config download`:

```bash
ocpp-csms config download --cp CP001 --output baseline.json
ocpp-csms config diff baseline.json newer.json
ocpp-csms config diff baseline.json --cp CP001
ocpp-csms config diff baseline.json --json
```

Two files are compared entirely offline. With one file, the command runs a fresh OCPP GetConfiguration query against `--cp`, or the charge point recorded in the snapshot, and compares the live response. Added, removed and changed keys are reported, including read-only flag differences. Passwords and other sensitive keys are masked in comparisons regardless of how the input snapshots were captured. Exit status: 0 for identical, 1 for differences; invalid inputs or live query errors are reported as errors. No charger configuration is modified.

### Explain a transaction

```bash
ocpp-csms txn explain 42
ocpp-csms txn explain 42 --json
ocpp-csms txn explain 42 --verbose
ocpp-csms txn explain 42 --context 5
```

`txn explain` analyzes archived StartTransaction/StopTransaction evidence, recovery state, overlapping open transactions, meter start/stop anomalies and transaction-related events. `--context MINUTES` adds nearby same-charger events, labeled contextual rather than conclusively associated. Findings distinguish observations from inferences. No active charger interrogation, new tables or automatic recovery are involved.
