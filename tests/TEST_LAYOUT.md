# Test ownership after the source refactor

Test directories follow *behavioral* boundaries rather than mirroring each source module one-for-one:

- `evidence/`: persisted events, diagnostics, timeline formatting.
- `control/`: dispatch, transport, remote commands, smart charging and fallback.
- `session/`: session state, inherited OCPP routes and charger WebSocket lifecycle.
- `server/`: server boundaries, WebSocket wrappers and active connection registry.
- `transactions/`: SQLite-backed transaction queries, diagnostic archives and reports.
- `rfid/`: authorization, charger-local list history/cache and reports.
- `ocpp_discover/`: packet discovery, parser compatibility, lifecycle and redirect operations.
- `cli/`: command parsing and public CLI output contracts.

## Phase 5 review

- The small decorator *experiment* is now a regression for inherited OCPP routing (`session/test_routes.py`), not a temporary experiment left behind.
- The raw-frame WebSocket wrapper regression belongs under `server/test_server.py`, not session state tests.
- Tiny ARP/WebSocket parser contracts and RFID history tests have been combined where assertions share their behavioral scope.
- Keep `transactions/test_package_imports.py` as a regression for the relocated transaction module import surface; imports are a supported integration contract.
- Retired transaction recovery-worker and inferred-stop tests have been removed. Preserve SQLite-authoritative retry, orphan evidence, authorization rejection, exception handling and other supported negative-path tests.
- Retired Gway-001 workflow-absence tombstones and old deployment-flag absence assertions were removed. Test current deployment behavior instead.

For future consolidation, compare collected test node IDs before/after changes, and ensure every removed node corresponds to an intentionally merged assertion or explicitly unsupported behavior.
