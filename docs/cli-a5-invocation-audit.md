# CLI A5.1 — executable invocation audit

Tracking: [#216](https://github.com/arthexis/ocpp-csms/issues/216). Baseline: `main` at `02d96bb` after A4d. This is an inventory and test-only slice; no runtime CLI changes.

## Verified migration state

| Surface | Evidence | A5 action |
| --- | --- | --- |
| Canonical transaction operations | `cli/__init__.py` routes `txn start/stop/explain/recover` through `build_txn_operations_parser` before the root parser. `tests/cli/test_txn_control_alias.py` still compares against standalone `start`/`stop`. | Preserve working behavior in A5.1; A5.3 should cover global options, exit semantics, and canonical paths. Do not remove legacy entry points without a consumer audit. |
| Reservation grammar | `reserve ID` is extracted before argparse; `tests/cli/test_reserve_canonical.py` covers detail, missing IDs, read-only behavior, and deprecated `reservation`. | A5.3 edge-case review only. |
| Maintenance commands | A4d #227–#231 added canonical command, transport, validation, and JSON contracts. | Exclude from duplicate implementation. |
| Parser facade | `cli/parser.py` exports `add_transaction_command`; code search found only its definition in-repo. | Candidate for later cleanup; external import compatibility unverified. |
| Export/forwarder | `cli/export.py` and `ocpp_forwarder/exporter.py` share `ocpp-csms/export/v1`. | Keep export stable; #224/#226 are a separate track. |
| Reporting | `report` and `mail` are registered. | Keep until the reports extraction has its own reviewed migration. |
| A1 contract document | `docs/cli-a1-command-contract.md` still calls A2–A5 future work and includes obsolete pre-migration entries. | Reconcile in A5.2 after executable call sites are checked. |

## Search scope and classification

Repository code-search discovery covered `src/`, `tests/`, `scripts/`, `ansible/`, README and docs for `reservation`, `diagnostics`, `transactions`, `export`, `ocpp-csms txn`, `ocpp-csms reserve`, `ocpp-csms start`, and `ocpp-csms stop`. Search results are candidate references, **not** proof of executable use: many are database names, schema identifiers, historical tests, or service operations.

A5.1 adds a focused static guard for literal **shell-style** executable invocations in the repository's deployment and operator-script surfaces. It deliberately excludes historical documentation, unit tests, Python import names, and the canonical A1 matrix. Dynamic subprocess argument arrays and externally hosted callers require manual review before removing aliases.

## Subsequent chunks

- **A5.2:** update verified stale executable examples and reconcile A1's status matrix with shipped CLI; keep export/mail/report intact.
- **A5.3:** cross-command integration and edge-case contracts for `txn`, `reserve`, global options, output and exit codes; consider facade removal only after consumer checks.
- **A5.4:** full required CI, test durations, deployment/integration scan, closure report against #216.

## Guard boundaries

The guard blocks only exact deprecated top-level shell commands `reservation`, `transactions`, `transaction`, `txns`, `diag`, and `diagnostics` after `ocpp-csms` in executable script and deployment text. It does not flag OCPP actions named Diagnostics, `ocpp-discover diagnostics`, paths such as `transactions/`, or prose. Extend this rule only with proven obsolete spellings and a regression test.
