# Native federated MCP provider (issue #394, stage 1 draft)

The provider is a subprocess using newline-delimited JSON-RPC 2.0 over stdio, not a listening service. GWay's current source adapter initializes protocol 2025-03-26, sends notifications/initialized, discovers tools/list and invokes tools/call. It enforces caller scopes and revalidates declarations. Provider names are composed as source_id + "__" + local name. The source ID must match the actual registry syntax (lowercase underscores allowed, hyphens not allowed); use **ocpp**, with a future ocpp.json manifest, not ocpp-csms.json. Multinode prefixes/routing remain to be verified before installing.

The provider and CLI share the existing appliance_status Python API and status derivation, not shell or CLI output. The provider passes a read-only SQLite connection, requires the current schema, excludes RFID and free-form info, distinguishes stored observations from physical state, and cannot send OCPP requests. Explicit absolute data-dir comes only from fixed startup argv. List results fail closed at 100 entries rather than silently truncate; SQLite busy timeout is 1s and query progress deadline 3s; GWay's external stdio deadline is 10s. This draft does not yet optimize fleet queries to avoid materializing every known charger; review before acceptance. No manifest is installed in this stage.

Future execute operations must share ocpp_csms.control.send_control and existing dispatch validation; use explicit charge-point ID and connector/transaction where applicable. Existing socket is mode 0660; TLS and data-reset additionally check peer UID. Audit service identity and access before enabling execute. Do not introduce shell passthrough, arbitrary OCPP actions, retries on uncertain delivery, delayed dispatch or a second listener. Distinguish rejected, acknowledged, unknown delivery and subsequently observed outcome. Only stored evidence tools are read-only; live wire calls (including GetConfiguration) require execute initially.

Stage 1 is not accepted until real database fixtures, stdio subprocess integration, resource limits, redaction, CLI regression and CI are verified. Stage 2 verifies federation and routing, Stage 3 adds execute tools, Stage 4 packages atomic manifest and rollback, Stage 5 validates on simulator and isolated canary. No production deployment is authorized.

References: GWay Remote gway_remote/mcp_source_stdio.py, mcp_source_registry.py, mcp_tool_scope.py, mcp_node_providers.py; CSMS src/ocpp_csms/status.py and control/transport.py.

## Stage 1 and 2 implementation follow-up

The shared appliance_status API now accepts optional explicit charger IDs and an opt-out of RFID policy reads; defaults preserve CLI behavior. MCP selects known IDs in its read-only snapshot before constructing status, limits lists to 100 IDs and total connector/open transaction rows to 1000, limits SQLite values to 64 KiB where Python exposes setlimit, and uses the external stdio deadline as a final bound. Python 3.10 lacks setlimit; acceptance must consider that limitation. No CLI output parsing.

Stage 2 adds transaction_list and event_list as bounded, metadata-only projections of stored evidence. Explicit charge_point_id required, limit 1–100 (default 20), optional exclusive before_id; descending local numeric ID order, not event-time order. next_before_id is null when exhausted. No payload, RFID or free-form diagnostic data. No cross-node transaction ID inference.

### Verified federation source contract

GWay Remote `gway_remote/mcp_federated_catalog.py` and `tests/test_mcp_multinode_federation.py` establish stable routes keyed by authenticated node and provider. With source ID ocpp, gway-001 advertises ocpp__charger_list, gway-002 advertises gway_002_ocpp__charger_list, gway-005 advertises gway_005_ocpp__charger_list and ocpp-abb-1 advertises ocpp_abb_1_ocpp__charger_list (same pattern for all five tools). The gateway derives the route from tool name, rejects duplicate providers and collisions, requires supports_provider_read for read-only dispatch, and never falls back to execute. No node argument, no change to GWay required for this naming/routing. Actual live node enrollment and catalog acceptance remain Stage 5, and manifest lifecycle Stage 4; no manifest is published now.

Hermetic tests cover real EventStore SQLite, CLI contract parity, denial of writes, schema mismatch, bounds, paging, redaction, and stdio subprocess handshake and oversized frames. CI remains the acceptance gate.
