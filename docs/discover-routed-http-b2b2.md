# Stage B2-B2 — Effective routed HTTP authorization (Draft only)

The operator-owned policy remains **default-off**. `enabled=true` and
`dedicated_charger_network_verified=true` are **not proof** of a physically
dedicated charger network. A fabricated `InterfaceTopology` is also not
trusted to grant runtime permission.

`routed_http_runtime.collect_kernel_observation` performs only read-only
sysfs and procfs inspection. It records interface identity (`ifindex`,
`iflink`), type, operational state, physical-device presence, master/lower/
upper relations and route interfaces. Unknown or unreadable fields fail closed.
These observations cannot prove external switch/peer exclusivity, so the
collector marks peer exclusivity **unproven**. Accordingly B2-B2 deliberately
mints **no active authorization** until independent physical/peer proof has an
approved mechanism in a subsequent, separately reviewed stage.

`service` passes an effective authorization (currently `None`) to the
passive SYN observer and first-contact candidate selector. Raw operator policy,
environment variables and manually built topology do not authorize TCP/80.
The provisional apply boundary rechecks authorization and live interface
identity immediately before any nft call. Even with rechecks, kernel topology
could change after verification; crash/restart handling is deferred to B2-C.

Port 8888 local opt-in remains unchanged. SSH/TLS are never routed through
this path. Existing durable receipt, nftables persistence, handoff and
reconciliation are not modified. No node/deployment or physical validation
is claimed by this Draft implementation.
