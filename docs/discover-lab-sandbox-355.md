# Issue #355 — isolated synthetic SQLite / nft smoke

This is a **separate, opt-in synthetic lab**; it never auto-starts on appliance
installation or in CI. It requires `bubblewrap` (`bwrap`), `nft`, and a
Linux kernel permitting unprivileged nested user/network namespaces. Missing
capabilities fail the lab rather than falling back to host networking.

Run in a disposable development environment, **never on gway-001/004,
Watchtower, or with a live charger's credentials**:

```bash
python -m ocpp_discover.lab_sandbox
```

The launcher uses fresh user/network/mount/PID/IPC namespaces via
`bwrap --unshare-all --clearenv`, a read-only mount of the host root, private
tmpfs mounts for `/tmp`, `/run`, `/var/lib`, and `/etc`, a new `/proc`
and `/dev`, and no shared network or shell. The child checks that its
network, mount and user namespaces differ from recorded parent identities;
`/proc/self/mountinfo` must also confirm an actual read-only root and
`tmpfs` filesystem types for every writable state mount. An arbitrary
`--inside` invocation without these properties fails closed. A
disposable schema-8 SQLite file is created under the private `/tmp` and
removed by namespace teardown. No CSMS account or actual production DB
is opened for writing.

The **synthetic EV Ready-shaped evidence** consists of a TCP/80 plaintext
WebSocket upgrade to `/ocpp/EVREADY_G3_SYNTHETIC`, a matching peer port,
and one fresh inbound OCPP event bound to a unique session. The lab checks
that pre-event evidence is rejected and reused TCP peer tuples are
ambiguous. A scoped `nft` table is validated, installed, inspected, then
deleted **inside the new network namespace only**, including cleanup after
inspection errors or a partially failed nft apply. It never calls field promotion or persistence writers.

The CI tests in `test_lab_sandbox_355.py` do **not** create namespaces or
execute nftables. They monkeypatch nft and the sandbox guard and test the
command restrictions, malicious/invalid namespace claims, disposable SQLite
proof and firewall cleanup. A green CI is not proof that a real kernel can
run the optional sandbox or that actual packets traverse the temporary NAT.

## Not yet physically accepted

This lab **does not replay actual TCP packets or run a real OCPP server**.
It exercises synthetic parsed WebSocket and SQLite evidence and actual
namespace-confined nft only when explicitly invoked. True end-to-end packet
delivery and BootNotification on a disposable CSMS still require a separate
isolated integration run with receipts; no traffic, charging or forwarding
behavior is implied. Real EV Ready gateway, OCPP events, Reports forwarding,
and reboot/recovery remain field-only acceptance gates. Keep #355 open.
