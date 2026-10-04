# Plaintext OCPP redirect field runbook

This runbook implements the field procedure for issue #53. It is intentionally limited to temporary interception of one observed **plaintext HTTP WebSocket** OCPP flow. It does not configure the host as a gateway, provide DHCP, alter NetworkManager, persist nftables state, or change the CSMS.

## Safety boundary

The redirect helper only becomes useful after the field host can already see the charger's original routed traffic.

Before using `field.redirect`, the replacement field host must already occupy the charger-facing L2/L3 gateway role expected by the charger. In the observed EV Ready topology this means preserving the historical gateway IPv4 role and the required DHCP/NAT behavior. The helper does not automate those deployment-specific changes.

Do not proceed if the charger is using TLS/WSS, if more than one plausible WebSocket source is observed, or if the local CSMS listener is not ready.

The helper uses two external host tools:

- `tcpdump` for bounded passive discovery;
- `nft` for syntax validation and temporary redirect installation/removal.

No Python dependency is added for either operation.

## 1. Establish the gateway role

Configure the field host using the site's normal network-management procedure so it provides the charger-facing gateway role the charger already knows.

Confirm that:

- the charger-facing interface is up;
- the host has the expected gateway IPv4 address;
- the charger can reach the host at L2/L3;
- forwarding/NAT/DHCP requirements for the site are already satisfied outside this helper;
- the local `ocpp-csms` listener is already running on the port that will receive redirected traffic.

A replacement host does not need to clone the previous NIC MAC address. A new ARP response can update the charger neighbor entry. If the charger keeps stale neighbor state, use the site's approved link-cycle, charger restart, or controlled gratuitous-ARP procedure before continuing.

## 2. Capture before mutating anything

Create a fresh run directory and passively observe a bounded window:

```sh
python -m field.redirect capture \
  --interface eth0 \
  --listen-port 9000 \
  --seconds 30 \
  --run-dir /path/to/redirect-run
```

The command watches outbound TCP/80 and TCP/443 traffic but only qualifies plaintext HTTP WebSocket Upgrade requests on TCP/80.

A successful capture writes:

```text
/path/to/redirect-run/redirect.json
```

Inspect this receipt before proceeding. It records:

- charger source IPv4;
- observed cloud destination IPv4 address or addresses;
- HTTP `Host` and request path evidence;
- charger-facing interface;
- intended local listener port;
- capture timestamp.

Stop if any of those values do not match the field observation you intended to capture.

The capture command refuses:

- multiple qualifying source IPv4 addresses;
- TLS/opaque TCP/443-only traffic;
- ordinary HTTP without a WebSocket Upgrade;
- an unavailable local listener;
- invalid parameters;
- reuse of a run directory that already contains `redirect.json`.

## 3. Validate the exact firewall candidate

Render and syntax-check the rules without installing them:

```sh
python -m field.redirect validate /path/to/redirect-run
```

This reloads `redirect.json`, validates it again, and requires the destination IPv4 set to match the captured WebSocket request evidence exactly. A hand-edited receipt cannot silently widen the destination set.

The command renders one non-persistent table named:

```text
table ip ocpp_field_redirect
```

The single redirect rule is constrained by all of the following:

- captured input interface;
- captured charger source IPv4;
- captured destination IPv4 set;
- TCP destination port 80;
- captured local listener port.

The generated ruleset is checked with `nft -c -f -`. Review the printed ruleset before applying it.

## 4. Apply explicitly

Only after the receipt and rendered rules have been inspected, install the temporary table:

```sh
sudo python -m field.redirect apply /path/to/redirect-run
```

`apply` requires root and repeats all important preconditions immediately before mutation:

1. reload and validate `redirect.json`;
2. confirm the local listener is still available;
3. confirm `table ip ocpp_field_redirect` does not already exist;
4. rerun `nft -c` on the exact ruleset;
5. load exactly that checked ruleset.

If the dedicated table already exists, stop and inspect it manually rather than attempting to replace or merge it.

The helper never flushes an nftables ruleset and never changes another table.

## 5. Verify interception

After `apply`, cause the charger to make its normal OCPP connection attempt without changing its configured cloud URL.

Verify all of the following:

- the charger connects to the local `ocpp-csms` listener;
- the original HTTP `Host` and OCPP request path remain visible as sent by the charger;
- the CSMS identifies the charger correctly from the request path;
- normal OCPP traffic is received;
- unrelated host traffic is unaffected.

Retain `redirect.json` with the field evidence. Preserve relevant packet/CSMS evidence according to the field exercise being performed.

If the charger does not connect, do not broaden the rule. Remove the dedicated table and investigate topology, ARP/gateway state, listener readiness, or whether the charger has moved to TLS/WSS.

## 6. Remove exactly the temporary table

When the capture/interception window is complete:

```sh
sudo python -m field.redirect remove /path/to/redirect-run
```

Removal requires the same run receipt and deletes only:

```text
table ip ocpp_field_redirect
```

It refuses an absent table rather than performing broad cleanup.

Confirm afterward that the table is gone and restore any separate gateway/network changes using the site's deployment procedure. Those changes are outside `field.redirect` and therefore are not reverted automatically.

## Abort conditions

Remove the redirect and stop the exercise if any of the following occurs:

- captured source or destination evidence is not what was expected;
- TLS/WSS replaces the observed plaintext flow;
- more than one charger/source becomes ambiguous;
- the local listener becomes unavailable;
- the redirect affects traffic outside the intended charger flow;
- charger behavior becomes unsafe or operationally unexpected.

Do not respond by broadening the source, destination, interface, or port match.

## Expected field sequence

```text
site-specific gateway preparation
        ↓
local CSMS listener ready
        ↓
field.redirect capture
        ↓
inspect redirect.json
        ↓
field.redirect validate
        ↓
inspect checked nft rules
        ↓
field.redirect apply
        ↓
verify charger reaches local CSMS
        ↓
collect evidence
        ↓
field.redirect remove
        ↓
site-specific network restoration
```

The persistent artifact from this helper is the evidence receipt. The nftables redirect itself is deliberately temporary and non-persistent.
