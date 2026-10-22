# Operations

Operational procedures for the installed OCPP 1.6J CSMS. **Inspect actual CLI help and the installed service before acting**: examples may lag the current executable. A command acknowledgement does not prove a charger physically complied.

## Evidence-first incident workflow

1. Identify the host and installed application revision, then inspect `systemctl status ocpp-csms` and `journalctl -u ocpp-csms`.
2. Run `ocpp-csms status`, followed by `ocpp-csms inspect --cp ID --offline` for a non-interrogating investigation.
3. Review `ocpp-csms events --cp ID --since 1d` (or `events ID --since 1d`, as supported by the installed CLI) and relevant `txn` evidence. A time filter must not silently truncate the evidence under the current command contract.
4. Distinguish persisted observations from inferred charger state. Confirm physical outcomes separately before declaring repairs successful.
5. Before any reset, authorization change, firmware update, or data operation: establish impact, ensure a recovery path, and preserve evidence. Never reset a production data directory merely to clear an alert.

## Installation, exposure, and enrollment

## Installation and public WSS exposure

Run `./deploy.sh` as a non-root operator. It invokes
`ansible/playbooks/install.yml` on cloud and satellite hosts. CSMS always
listens on `0.0.0.0:9000`. Discover is installed whenever `eth0` exists,
even without a connected charger. Discover redirects supported plaintext
traffic; it does not terminate TLS.

Public TLS/WSS is optional and independent of installation:

```bash
./deploy.sh
ocpp-csms auth basic --limit 3d
sudo systemctl restart ocpp-csms.service
./deploy.sh --expose ocpp.example.com --email operator@example.com
```

`--expose` invokes `ansible/playbooks/expose.yml`, which configures nginx,
Certbot and port 443 WSS forwarding to `127.0.0.1:9000`. This is nginx's
local upstream destination, **not** a change to the CSMS listener. Port 80
serves ACME HTTP-01 challenges only. Configure DNS and external firewall rules
for the desired reachability. Direct WS on port 9000 remains available wherever
network policy permits it; nginx does not disable direct connections.

Use `--no-certbot` only with an already-provisioned compatible certificate.
Verify `nginx -t`, `certbot renew --dry-run` when applicable, and a full
authenticated WSS session before commissioning a charger. Separate clients
requiring isolated credentials or databases need separate CSMS instances.

### Internal WSS smoke test (no DNS or public ports)

The installed delegate can be checked using its native TLS listener at
`127.0.0.1:9443`. This is independent of nginx, Certbot and the public
`--expose` workflow. The listener must already be enabled, and its certificate
must have a DNS SAN that matches the test's `--tls-hostname`. No DNS record is
needed: the client uses that hostname for TLS verification but connects the
socket directly to `127.0.0.1`. The certificate (or its issuing CA) must be
provided explicitly as a trusted PEM; insecure TLS verification is not supported.

Use an **existing dedicated test CP identity already provisioned in Basic auth**.
Do not use pending first-claim credentials or overwrite a real Vendor charger's
identity. A single-instance Basic-auth configuration binds one identity, so
this smoke test is not suitable for an instance already provisioned for a
different production charger without an approved separate test identity or
isolated test installation. The smoke sends actual simulated OCPP start/stop
events to the installed database.

```bash
ocpp-csms tls status
ocpp-csms tls check --ws-port 9000
python3 scripts/internal_wss_smoke.py \
  --cp SIM-WSS-LOCAL \
  --tls-hostname localhost.test \
  --ca-file /path/to/trusted-local-test-cert.pem \
  --port 9443
```

The script prompts securely for the existing Basic-auth password and verifies
BootNotification, Heartbeat, Authorize, StartTransaction, MeterValues,
StopTransaction, and persisted events. It never opens a public port, starts a
new server, changes TLS/auth configuration, or modifies systemd. Use a
disposable or dedicated CP identity for transactions and confirm its port
is bound to loopback before running the test.

### HTTP Basic authentication (WSS)

The default for existing local deployments is `none` (no `auth.json`).
For a public standalone endpoint, explicitly configure Basic auth **before
opening TCP/443**:

```bash
ocpp-csms auth basic --limit 3d    # pending first-claim enrollment
# OR: ocpp-csms auth basic --cp Vendor-001  # pre-register a known identity
ocpp-csms auth status
sudo systemctl restart ocpp-csms.service
```

With `--limit 3d`, the first successful WSS Basic authentication during the
next 72 hours claims the supplied URL's final charge-point ID and permanently
binds the stored credential to that identity. An unclaimed credential expires
after 72 hours and is rejected; once claimed, normal authenticated reconnections
do not expire. In pending mode the Basic username must equal the identity in the
URL. The first-claim risk is intentional: keep the credential private until Vendor
commissions the charger. The known-ID `--cp` alternative needs no enrollment
window.

The command prompts twice for the password without exposing it on the command
line; the persistent configuration is stored at `/etc/ocpp-csms/auth.json`
with mode 0600 and a salted scrypt password hash. Run it as the CSMS service
identity or a user permitted to write the persistent configuration directory.
The password is not recoverable from the hash; repeat the command to rotate.
Existing sessions are interrupted by restart, so schedule rotations.

In Basic mode, the WSS WebSocket upgrade requires an HTTP Authorization Basic
header. Once enrolled, the username must equal both the bound charge-point ID and the
last URL path segment; missing or wrong credentials return 401. Plaintext WS
returns 403 in Basic mode regardless of credentials. Bad persisted auth
configuration causes startup failure instead of anonymous fallback.
If the auth file is absent the original anonymous-local behavior remains;
therefore **never expose a cloud CSMS publicly before setting Basic auth**.

This is Basic authentication, **not** mutual TLS/client certificates. mTLS
remains a separate feature tracked in the Lightsail epic. An Vendor charger
must be configured to send Basic credentials during the WebSocket handshake.

### Canonical charger URL

Give the charger operator the **full canonical** WSS URL:

```text
wss://<hostname>/<charge-point-id>
```

For example, if Vendor identifies its charge point as `Vendor-001`, the configured
URL should be `wss://ocpp.example.com/Vendor-001` (replace the example hostname
with the actual DNS record). **Confirm whether the Vendor charger appends its
identity automatically**: if it does, enter only the documented base URL to
avoid doubling the ID.

The current CSMS takes the **last nonempty URL path segment** (before the
query string) as its charge-point identity. Thus alternate paths can connect,
but a different final segment creates a **different logical charger identity**.
A wrong URL is not an alias of the canonical one. The same final segment
on two concurrent connections represents a duplicate identity; do not treat
this as independent chargers or rely on its session state. Use the exact
canonical URL for every reconnection and rollback procedure.

This convention is **not access control**. The CSMS currently allows unknown
charge-point identities by default, so arbitrary paths do not establish
authorization. Before exposing a public endpoint, establish a suitable
identity/authentication policy and verify the actual Vendor firmware requirements.

The native WSS listener requires a readable certificate/key matching the DNS
hostname. Verify certificate chain trust externally and test a full OCPP 1.6J
session over public WSS before changing the real charger configuration.
Binding public TCP/443 from an unprivileged process requires a deliberate
privileged-port strategy; the default native TLS port is 9443.

## Commands

Run `ocpp-csms --help` for the authoritative current surface.


Use `diagnose` for charger diagnostics and `avail` or `availability` for availability control. Maintenance commands accept `--cp`; `-j`/`--json` emits a machine-readable response. An accepted OCPP request acknowledges
submission, **not** physical completion. `Scheduled` availability means the
requested change is pending. Firmware update requires explicit `--confirm`;
`diagnose --location URL` requires a charger-reachable upload URL. Transfer URLs must
use HTTP(S) or FTP(S) without fragments, and retry values cannot be negative.


Common operations include:

```text
ocpp-csms status [CHARGER]
ocpp-csms status --charging
ocpp-csms chargers [--charging] [--json]
ocpp-csms cps ...
ocpp-csms charger CHARGE_POINT [--json]
ocpp-csms cp CHARGE_POINT [--json]
ocpp-csms txn [ID] [--active|--last] [--cp CHARGE_POINT] [-c N|--connector N] [--events]
ocpp-csms txn ...
ocpp-csms rfid report [RFID]
ocpp-csms rfid export [CHARGER]
ocpp-csms rfid version [CHARGER]
ocpp-csms rfid clear [CHARGER]
ocpp-csms config [KEY ...] [--cp CHARGE_POINT]
ocpp-csms config download [--cp CHARGER] [--show-sensitive] [--json] [--output FILE]
ocpp-csms profile templates
ocpp-csms profile help TEMPLATE
ocpp-csms profile send max-power --watts WATTS [--start ISO-8601] [--cp CHARGE_POINT]
ocpp-csms profile composite [--cp CHARGE_POINT] [-c N|--connector N] [--duration SECONDS] [-T|--local-time] [--json]
ocpp-csms profile clear ...
ocpp-csms txn start [--cp CHARGE_POINT] [-c N|--connector N] [--rfid TAG]
ocpp-csms txn stop ID
ocpp-csms reset [--cp CHARGER] [--hard]
ocpp-csms events [--cp CHARGER] [--since TIME] [--until TIME] [-n VALUE|--limit VALUE]
ocpp-csms report [--since TIME] [--until TIME] [--cp CHARGE_POINT] [--json]
ocpp-csms alerts [CHARGER] [--since TIME] [--until TIME] [-n VALUE|--limit VALUE] [-N] [--json]
ocpp-csms explain CHARGER --at TIME [--minutes N]
```

### Field maintenance (OCPP 1.6J)

Use the existing Unix control socket to send one-shot maintenance requests to a connected charge point.
Omit `--cp` when only one charge point is connected; otherwise specify `--cp ID` .

```bash
ocpp-csms trigger heartbeat
ocpp-csms trigger status -c 1
ocpp-csms trigger meter -c 1
ocpp-csms avail disable -c 2
ocpp-csms avail enable -c 2
ocpp-csms avail disable  # connector 0: whole charge point
ocpp-csms unlock -c 1
```

Trigger supports `boot`, `heartbeat`, `status`, `meter`, `diagnostics`, and `firmware` when the charge point supports them; `-c` applies to status and meter only. Both `avail` and `availability` select availability control. Availability returns `Accepted`, `Rejected`, or `Scheduled`; Scheduled means the charger has deferred the change (typically until an active transaction finishes). Unlock requires a physical connector ID greater than zero. All commands support `--json` for the raw OCPP response. A charger accepting a request does **not** prove the physical action completed; inspect subsequent events and connector status.

### Capability inspection and reconciliation

```bash
ocpp-csms capabilities [--cp CP001] [--json]
ocpp-csms reconcile [--cp CP001] [-c 1] [--json]
ocpp-csms rfid cache clear [--cp CP001]
```

`capabilities` fetches `SupportedFeatureProfiles` from a connected charge point, labeling features **Advertised**, **Not advertised**, or **Unknown**. Advertised features are not necessarily verified as operational. `reconcile` is a **read-only local snapshot** of stored connector notifications and open transaction archives; it does not interrogate the charger, assert physical state, infer missing StopTransaction frames, or modify transactions. Disconnected charge points are marked uncertain. `rfid cache clear` issues OCPP `ClearCache`, and does not change the charger local authorization list managed by `rfid clear`.

### Status and charger views

`cp` shows one charge point and its connectors; `cps` lists charge points. `-c` aliases `--connector`, and `--cp` selects a charge point.

### Transactions and events

`txn start` and `txn stop ID` are the canonical remote control operations, with immediate dispatch. Transaction stop requires a positional ID. `txn ID`, `txn --active`, and other history queries remain unchanged.


`txn` exposes transaction history and active/last transaction views. `events` exposes recorded runtime and OCPP evidence, while `explain` provides a time-centered diagnostic view for a charger. Event-specific `--txn` filters are unchanged.

By default, `events` shows the latest **200 events**. `-n` and `--limit` are interchangeable: an integer caps the number of matching events, while a duration (`s`, `m`, `h`, `d`, or `w`, case-insensitive) returns **all** events in that time window without a count cap. `--since` and `--until` accept either relative durations such as `3d` or ISO-8601 timestamps. A duration limit ends at `--until` when given, otherwise at the current UTC time; `--since` further restricts the window. The latest events are selected first and printed chronologically. Compact grouping and JSON output use the same filters. The default text timeline groups adjacent routine `MeterValues` requests and their empty acknowledgements for the same charge point, connector, and transaction (e.g. `MeterValues C1 tx=42 ↔ ×4`). This is an observed adjacent exchange, not a protocol-message-ID correlation. Unmatched or unusual records remain visible, and grouping never changes the underlying event count or time-window boundaries. Use `--detail` to inspect individual event detail; check command help for the available output flags; `--json` and `--raw --json` retain ungrouped machine-readable evidence. `explain` uses the same compact text formatter.

```bash
ocpp-csms events                       # latest 200 events
ocpp-csms events -n 50                 # latest 50 events
ocpp-csms events -n 1d                 # all events from the last day
ocpp-csms events --since 3d            # latest 200 events within three days
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

Use `reserve` to list recorded reservations, `reserve ID` to inspect historical evidence, `reserve create` to send OCPP `ReserveNow`, and `reserve cancel ID` to cancel. Reservation IDs are operator-supplied positive integers, unique for the charger. Connector 0 lets the charger choose any connector. The charger must support reservations; an `Accepted` response acknowledges the request, not guaranteed future availability.

```bash
ocpp-csms reserve --cp CP001
ocpp-csms reserve 42 --json
ocpp-csms reserve create --cp CP001 -c 1 --rfid ABC123 --id 42 --until 1h
ocpp-csms reserve cancel 42 --cp CP001
```

Optional `--parent-rfid` maps to `parentIdTag`. Requests and responses use the existing OCPP evidence store. Reservation creation does not require a new service, scheduler, or local booking database. A reservation ID must be tracked by the operator for cancellation.

### Firmware management (OCPP 1.6J)

`UpdateFirmware` tells a connected charge point to retrieve a firmware image from a charger-reachable external URL. This may interrupt charging or reboot the charger. An explicit `--confirm` is required; retrieval is requested immediately.

```bash
ocpp-csms firmware update --location https://example.org/fw.bin --confirm
ocpp-csms firmware update --cp CP001 --location https://example.org/fw.bin --confirm
ocpp-csms firmware status --cp CP001
ocpp-csms firmware history --cp CP001 --since 7d
```

The CLI also supports `--retries`, `--retry-interval`, and `--json`. The OCPP `UpdateFirmware.conf` is an empty acknowledgement and **does not establish successful downloading or installation**. `firmware status` and `firmware history` inspect recorded `FirmwareStatusNotification` observations; no polling service or firmware hosting is installed. Firmware URLs are redacted in event evidence, including user info, path, and query tokens. Validate vendor/model compatibility and the charger's supported retrieval protocol before submitting an update.

### Charger diagnostic upload requests

`ocpp-csms diagnose` reads stored diagnostic evidence by default and sends **no OCPP request**. Use `--list` to request the same history view explicitly, or `--location URL` to send OCPP 1.6 `GetDiagnostics` to a connected charge point. The upload URL must be reachable by the **charger**; an external server receives uploads.

```bash
ocpp-csms diagnose --cp CP001
ocpp-csms diagnose --cp CP001 --list -n 20 --json
ocpp-csms diagnose --cp CP001 --location https://uploads.example.org/charger
ocpp-csms diagnose --cp CP001 --location https://uploads.example.org/charger --since 1d --until now --retries 2 --retry-interval 30
```

The old `diag request`, `diag history`, and `diagnostics` commands have been removed. Request evidence redacts upload credentials, path, and query strings. History reads stored GetDiagnostics requests/responses and DiagnosticsStatusNotification events. It does **not** establish strict request-to-notification correlation because persisted events lack OCPP message IDs, and an `Uploaded` notification does not prove the destination received the file. A future optional CSMS-managed diagnostic receiver provisioned by Ansible is outside this PR.

### Incoming maintenance notifications

The CSMS accepts and records OCPP 1.6 `DiagnosticsStatusNotification` and `FirmwareStatusNotification`, including unsolicited notifications. Their status transitions are available in `events`; `UploadFailed`, `DownloadFailed`, and `InstallationFailed` additionally appear in `alerts`. Ordinary progress is not an alert. These handlers only acknowledge and preserve evidence; they do not request diagnostics, host uploads, or initiate firmware updates.

### Alerts

`alerts` shows exceptional recorded events as individual occurrences, without
compact grouping. It shares the relative/ISO time and count/duration limit
semantics of `events`, including `-N/--no-limit`. The default selects the
latest 100 **matching alerts**, rather than the latest 100 unfiltered events.
Use `--cp`, a positional charge point, or the event-specific `--txn` filter to restrict
the result; `--json` exposes the versioned alert contract.

```bash
ocpp-csms alerts --since 3d
ocpp-csms alerts --since 3d -N
ocpp-csms alerts --cp CP001 --txn 123 --json
```

Ordinary charging starts remain events, not alerts.
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
```

### Configuration

`config download` issues a full OCPP `GetConfiguration` and records every returned key with both its `readonly` flag and current value.

When exactly one charger is connected, the charger ID may be omitted. With multiple connected chargers, an explicit charger is required.

Configuration commands are dispatched to the connected charge point without an inferred charger-state gate.

Sensitive values are masked by default using `[REDACTED]`. Use `--show-sensitive` only when the actual secret-bearing values are intentionally required.

Examples:

```bash
ocpp-csms config download
ocpp-csms config download --cp CHARGER
ocpp-csms config download --cp CHARGER --json
ocpp-csms config download --cp CHARGER --output charger-config.json
ocpp-csms config download --cp CHARGER --show-sensitive --output charger-config-private.json
ocpp-csms config download --cp CHARGER
```

`--output` writes a structured JSON snapshot. Human-readable terminal output remains available unless `--json` is requested.

### Remote start, stop, and reset

Remote commands are sent only to chargers connected to the current CSMS process and are never queued for later delivery. Accepted commands and resulting charger behavior are recorded as separate facts.

Remote start, stop, and reset commands have no scheduling options.

Control commands are dispatched immediately to connected chargers. There are no inferred charger-state gates or delayed/queued execution; the charger responds to each OCPP command.

Start and reset requests are dispatched to connected charge points without a busy-state gate; the charger decides whether to accept them.

### Smart Charging

The built-in `max-power` template is an Absolute `ChargePointMaxProfile` anchored by default at `2000-01-01T00:00:00Z`. The deliberately old fixed start avoids making immediate station-wide limits depend on close agreement between charger and CSMS clocks. Use `--start` with an ISO-8601 date-time including timezone to override that anchor; explicit values are normalized to UTC before being sent.

`profile composite -T` converts the returned `scheduleStart` to the CSMS host local timezone for human-readable output. JSON output remains unchanged and preserves the charger/OCPP timestamp exactly as returned.

Smart Charging is intentionally stateless on the CSMS side. The charger owns installed profiles and effective schedules; upper layers own site/business policy.

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

## TLS configuration

TLS configuration is optional and disabled by default. `tls config` records the
certificate, key, hostname and port without enabling the WSS listener.

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
under the CSMS service account to verify effective readability.

### Optional WSS listener

The `ocpp-csms.service` starts plaintext WS. If the persistent
`/etc/ocpp-csms/tls.json` configuration has `"enabled": true` and passes
certificate/key checks, the same process also starts WSS on the configured
port. Both listeners dispatch to the existing OCPP handler and share session
and transaction state. TLS readiness and bind failures are logged without
bringing down WS. `tls config` leaves TLS disabled by default. Before enabling WSS, verify
that the service account can read the private key and that the certificate
passes validation. Use `tls enable` to activate the listener.

### Live TLS management

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

### Persistent TLS provisioning

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

`txn explain` analyzes recorded StartTransaction/StopTransaction evidence, overlapping open transactions, meter start/stop anomalies and transaction-related events. `--context MINUTES` adds nearby same-charger events, labeled contextual rather than conclusively associated. Findings distinguish observations from inferences. No active charger interrogation, new tables or automatic recovery are involved.

### List reservation evidence

```bash
ocpp-csms reserve
ocpp-csms reserve --cp CP001
ocpp-csms reserve --all --since 7d
ocpp-csms reserve --json
ocpp-csms reserve 42 --cp CP001 --json
```

This read-only command reconstructs locally observed `ReserveNow`, `CancelReservation` and reservation-associated `StartTransaction` evidence. `Requested` means the CSMS sent the request without an unambiguously attributable response; `Accepted` is the charger's recorded response and **does not mean currently active**. `--all` includes rejected, canceled, used and expired requests. Each row displays the reservation ID and charge point needed for `reserve cancel ID --cp CP`; `reserve ID` reads the matching historical evidence without contacting the charger. The event archive lacks message-level request/response correlation for these commands, so concurrent ambiguous responses are not attributed by guesswork. No charger queries or new storage are required.

### Firmware preflight

```bash
ocpp-csms firmware check --cp CP001
ocpp-csms firmware check --cp CP001 --location https://example.org/firmware.bin
ocpp-csms firmware check --cp CP001 --offline --json
```

`firmware check` reads connection and transaction evidence, reconciliation conflicts, recorded BootNotification firmware version, and prior FirmwareStatusNotification status. For a connected charger (unless `--offline`), it requests only `SupportedFeatureProfiles` through GetConfiguration with a bounded timeout. The optional URL receives syntax validation only; the CSMS does not download the image or assert that the charger can reach the URL. Report statuses distinguish OK, warning, unknown and error. Exit codes: 0 for no known blockers, 1 for warnings, 2 for a blocked preflight. This operation does not establish firmware compatibility and never sends UpdateFirmware.

### Firmware retrieval timing

Firmware updates require `--confirm` and exactly one explicit timing option:

```bash
ocpp-csms firmware update --cp CP001 --location https://example.org/fw.bin --confirm
```

Firmware delivery sends the OCPP `retrieveDate` parameter with the current time. The request is immediate; actual installation timing remains charger-controlled.


### Uninstall

`./deploy.sh --uninstall` runs `ansible/playbooks/uninstall.yml` and removes
managed systemd services, replaceable application releases, Discover-owned
persistent nftables integration, and identifiable CSMS-managed nginx sites.
It does not delete SQLite databases, transaction archives, Basic authentication
credentials, or TLS certificate archives. No deployment profile is recorded
or cleared. Back up durable data before uninstalling.

### Gway-001 deployment and validation

Gway-001 is **not** a GitHub Actions self-hosted runner for this repository.
Hosted CI validates Python and Ansible changes without touching field equipment.
To request deployment of `main` to Gway-001, manually run the
[Deploy CSMS to Gway-001](https://github.com/arthexis/ocpp-csms/actions/workflows/deploy-gway-001.yml)
workflow. It dispatches the checked-out commit SHA to `arthexis/gway-remote`
via `ocpp-csms-deploy`; follow the remote system's outcome rather than
assuming the request proves successful installation.

Field simulator and external IP/DNS WSS acceptance tests are separate,
operator-authorized tasks for the local agent (see #204). They must not run
on ordinary PR CI or update production CSMS inadvertently.


### Resetting a dedicated test delegate before Vendor commissioning

After the remote simulator acceptance tests, reset **only a confirmed
test-only data directory**, and only after stopping the CSMS service and
confirming no real Vendor charger or active transaction is present:

```bash
sudo systemctl stop ocpp-csms
ocpp-csms data reset --all --dry-run
ocpp-csms data reset --all
sudo systemctl start ocpp-csms
ocpp-csms status
```

For a *single* charge point, use `ocpp-csms data reset --cp NAME --dry-run` to review
the operation, then `ocpp-csms data reset --cp NAME` to remove its
SQLite records and matching JSON archives while preserving other charge points.
Both modes back up the original dataset and require the CSMS to be stopped.
The charge-point identity is matched exactly, and ambiguous archives abort the
operation rather than risking an unrelated deletion.

An explicit `--dry-run` is non-mutating. The `--all` operation moves
the data directory into its sibling `ocpp-csms-data-test-backups/` folder and
initializes fresh SQLite/transaction storage. It rejects active services,
recorded active charges, or inconsistent persistence state. **It does not**
change deployment identity, nginx, certificates, firewall or Basic-auth
credentials stored outside the data directory. Rotate/re-enroll test
credentials separately before real Vendor commissioning. Do not run this command
on a production data directory or use it to conceal genuine charge history.


### Mandatory pre-reset archive

All executing `data reset` modes now create and verify a portable `.tar.gz` archive outside the active data directory before modifying evidence. The archive includes a manifest with SHA-256 checksums, scope, schema version, and a consistent SQLite backup captured with the SQLite backup API. The command reports `archive` in its structured result. Preserve the archive separately before restoring or retiring the host. Archive creation or verification failure aborts the reset. `--dry-run` does not create an archive or modify evidence.

### Undoing a data reset

Use the exact archive path returned by the reset command. Undo restores the **entire pre-reset snapshot**, even if the reset targeted one charge point. It is not a selective merge. Stop the CSMS first; undo refuses to run on an active service and rejects any changed or newly recorded evidence since the reset.

```bash
sudo systemctl stop ocpp-csms
ocpp-csms data reset --undo /path/to/backup.tar.gz --dry-run
ocpp-csms data reset --undo /path/to/backup.tar.gz
sudo systemctl start ocpp-csms
```

Undo requires the matching post-reset verification record beside the archive, checks every archived file against its SHA-256 manifest, and verifies SQLite integrity and the expected schema. It preserves the displaced post-reset data directory as a separate sibling `-undo-displaced` directory instead of deleting it. If any evidence has changed since reset, **undo fails rather than overwriting it**; recover manually using a separate copy and compare the evidence. Never restore a snapshot into another host or schema without an independent recovery plan.

### Fast fault investigation

Use `ocpp-csms faults` to list recent incoming `StatusNotification` faults without raw JSON or `jq` pipelines. `-n 5` shows the last five matching faults; add `--cp ID` to select a charger. Charger timestamps are shown separately from CSMS receipt time, since offline queues may arrive days late. `ocpp-csms events --detail` also shows `vendor_id` and `vendor_error_code` when supplied.

Charger-scoped commands use `--cp CHARGER` instead of positional charger identifiers. Omit `--cp` where the command supports all-charger inspection or can infer a unique connected charger. Transaction IDs, connector IDs and operation names retain their documented selectors.

## HTTPS charger diagnostic uploads (nginx DAV)

Public `deploy.sh --expose HOSTNAME` provisions a native nginx HTTPS PUT receiver at
`/ocpp-diagnostics/<unpredictable-request-id>/upload.bin`. The URL is a bearer
capability: anyone possessing it can attempt to upload before expiry; do not
paste it into tickets or logs. Requests have a two-hour expiry and an hourly
pruning timer; nginx denies GET and DELETE, caps request bodies at 100 MiB,
and rejects overwriting an existing target. Public port 443 is sufficient.
There is no custom HTTP upload backend. This is available only when the
public nginx expose role was deployed, not via local install alone.

Operator workflow after expose:
```bash
ocpp-csms diagnose request --cp CHARGER
ocpp-csms diagnose status --cp CHARGER
ocpp-csms diagnose list
ocpp-csms diagnose show
```
With exactly one connected charger, `--cp` can be omitted for a request.
The older `ocpp-csms diagnose --location URL` remains supported for
charger-specific or alternate transfer services.

A successful `GetDiagnostics` response does *not* prove the upload succeeded.
`diagnose status` confirms that a file appeared on disk but does not validate
the diagnostic contents. Use `diagnose show` to locate the received archive,
then inspect it with standard offline tools on a trusted machine. Never execute
scripts found inside charger archives. Incoming files are treated as untrusted.

**Compatibility:** nginx DAV accepts HTTP PUT. Chargers using POST or FTP/FTPS
will not be compatible with this endpoint. Test the particular charger firmware
and check nginx access/error logs (the bearer path is excluded from access logs).
Avoid exposing diagnostic archive contents publicly.

### Long-running GetDiagnostics replies

`ocpp-csms diagnose request` waits for the charger CALLRESULT without a
CLI-imposed response deadline (until response, connection loss, or operator
interruption); `ocpp-csms diagnose request --limit 120` caps that wait at
120 seconds. The charger can still report `Uploading` or `UploadFailed`
after the command exits. Use `ocpp-csms diagnose status` and
`ocpp-csms diagnose --list` to inspect later evidence. Upload status
notifications are correlated by charger and time only, not a request ID;
`Uploaded` alone does not prove a file was received by nginx. The HTTPS
upload directory expires independently of the response wait. A missing
CALLRESULT and a failed HTTPS transfer are different failure modes.

### Follow a diagnostic upload

`ocpp-csms diagnose request` returns when either the OCPP `GetDiagnostics`
CALLRESULT is received **or** a charger `DiagnosticsStatusNotification`
indicates `Uploading`, `Uploaded`, or `UploadFailed`. The CLI no longer
needs to wait for a missing CALLRESULT once the charger provides independent
upload evidence. By default the initial acknowledgement has no CLI timeout;
`--limit 90` caps that *initial* wait at 90 seconds.

`ocpp-csms diagnose request --follow` continues printing upload status
notifications until a local file is received, the charger reports
`UploadFailed`, or the upload destination expires. A charger-reported
`Uploaded` status alone is not treated as proof of local file receipt.
`--limit` does not cut short `--follow` once the initial signal has arrived.
Press Ctrl-C to stop following; the upload receiver continues independently.

### POST support (ABB and other chargers)

The public HTTPS upload endpoint supports nginx-native PUT and HTTP POST
(including ABB's `upload.bin/` trailing slash). nginx terminates TLS and
forwards POST only to the on-demand Unix socket
`/run/ocpp-csms-diagnose.sock`. The socket is activated by systemd using
`ocpp-csms-diagnose.socket` and `ocpp-csms-diagnose.service`, both
provisioned by `deploy.sh --expose HOSTNAME` or an ordinary redeployment
that reuses the saved exposure hostname. No other public port is opened.

The POST receiver rejects invalid paths, missing request directories, empty
or oversized uploads, unexpected content types, and attempts to overwrite
an already received file. Multipart uploads retain only the file payload,
not client-provided filenames. URL bearer tokens must not be logged.

On the charger host use `ocpp-csms diagnose request --limit 120 --follow`
after deploying the updated expose role. Test the receiver locally before
retrying real charger uploads. A charger-reported `Uploaded` does not prove
that nginx stored a file.
