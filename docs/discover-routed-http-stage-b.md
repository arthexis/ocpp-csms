# OCPP Discover routed HTTP — Stage B1 contract (issue #355)

## Objective and boundary

Stage A is merged as PR #356. Stage B's unresolved requirement is a
**supported, explicit, persistent operator opt-in** for plaintext TCP/80
traffic arriving on a *verified dedicated charger-facing* network.
The current product supports a process environment variable,
\`OCPP_DISCOVER_ALLOW_ROUTED_HTTP_80=1\`, but this is not yet a durable
operator-verified deployment configuration.

**B1 is offline contracts and hermetic tests only.** The new
\`ocpp_discover.routed_http_policy\` module is **not wired to Discover's
service or Ansible role**. It does not install or enable a redirect, alter
boot configuration, persist a destination, restart a process, or touch a
charger. Consequently, Stage B is NOT accepted and routed HTTP remains
default-off in production.

## Proposed policy v1

Target file: \`/etc/ocpp-discover/routed-http-policy.json\`.
The file is optional (missing = disabled); when present, it must be a
root-owned regular file with permissions exactly \`0600\`, no symlinks,
at most 4096 bytes, valid UTF-8 JSON, no duplicate or unexpected keys:

\`\`\`json
{
  "version": 1,
  "enabled": false,
  "interface": "eth0",
  "dedicated_charger_network_verified": false
}
\`\`\`

Enabling requires *both* literal JSON booleans \`enabled=true\` and
\`dedicated_charger_network_verified=true\`, and a valid, exact interface
name. Invalid/ambiguous policy, wrong ownership or mode must fail closed.
The attestation is an **operator assertion**, not proof that a network
is physically dedicated. Do not enable until independent topology and
routing checks are available in B2.

## Remaining B2 / B3 acceptance, still blocked

- B2: integrate the policy into deployment and Discover startup, retire
  unsafe environment-only activation, require operator-confirmed network
  isolation and reconcile config changes/reboots without mutating unrelated
  firewall state
- B2: show isolated disabled/enabled routed SYN, exact temporary scope,
  positive/negative WebSocket and session proof, bounded timeout, no-match
  removal, signal/crash cleanup, restart/reboot reconciliation
- B2: ensure reserved synthetic addresses and synthetic charger IDs can
  **never** enter the durable field adaptation; keep field and simulator
  evidence distinguishable
- Stage C: physical EV Ready gateway, actual OCPP 1.6J connection/events,
  forwarding and network acceptance, separately authorized

No field node access, nftables mutation, HTTPS/TLS/SSH interception,
production credential use, deployment, service start or workflow dispatch
is authorized by this B1 contract.
