# Transaction simplification — test audit and red-first plan

Tracking: #247 (architecture), #244 (implementation). Supersedes unmerged PR #246. This branch starts from main, not from #246.

## Development discipline
1. Commit contract tests **before** changing production code. The first intentional CI failures are expected and documented; do not skip, xfail, delete, or weaken the new tests to make CI green.
2. Work in reviewable slices: red test commit, then minimal production change, then green validation. During the initial red phase, existing unrelated tests should remain green.
3. Distinguish tests of the *public contract* from tests of the old internal recovery implementation. Remove only the latter, with replacement coverage for evidence preservation and query/report semantics.
4. Run Python 3.11, Python 3.13, and general CI at completion. Do not merge red tests.

## Contract matrix
| Scenario | Expected result |
| --- | --- |
| Distinct starts on same CP/connector at T and T+1s | Two different monotonically increasing OCPP IDs; two retained starts |
| Exact same CP/full start payload retransmitted immediately, hours later, after restart | Original OCPP ID; each received request remains visible as evidence |
| Same timestamp but changed start payload | New transaction, conflict/diagnostic evidence retained |
| Different CP, identical start payload | Different transaction |
| New start on connector 1 while connector 2 is active | Independent connector histories |
| Previous start has no StopTransaction | Next distinct start still accepted, previous record not mutated or synthetically stopped |
| MeterValues references unknown ID | Orphan/unresolved evidence only; no new transaction or inferred start |
| StopTransaction references unknown ID | Orphan/unresolved evidence only; no new transaction or inferred start |
| Genuine StopTransaction arrives late | Retained as observed stop and takes precedence over report inference |
| New start on same CP/connector after missing stop | Reporting derives effective stop from new start receipt time, labels 'next-start inferred', does not persist stop |
| No meter readings or meterStop | Energy unknown, not invented zero |
| Out-of-order charger timestamps / late receipt | Separate charger/receipt time; deterministic conservative derived boundary |
| Restart after durable start | Next ID greater than all previously allocated local IDs; exact retry still resolves |
| SQLite unavailable during new start | ID allocation does not require SQLite; archive/evidence failure is handled explicitly |
| Positive signed 32-bit ID limit | Explicit predictable exhaustion behavior; no wraparound or random fallback |
| Control after stale open transaction | Credible live connector state, not historical open status, governs safety |
| Export pagination/replay | Only real evidence advances cursor; no synthetic stop event |

## Audited existing test files (main)

### Replace/remove obsolete recovery behavior
- `tests/transactions/test_recovery_worker.py` (4 tests): **remove** aged/disconnected inferred-stop worker, restart reconciliation, and activity-based recovery tests. Replace with no-synthetic-stop and control-unblocking tests.
- `tests/transactions/test_inferred_recovery.py`: **remove/replace** inferred-stop archive+SQLite mutation test. Keep schema-version coverage in `tests/test_schema.py` rather than duplicating it.
- `tests/test_recovery_cli.py` (3 tests): **remove** recovery policy/preview/offline write tests once commands are removed. Cover supported CLI and absence of obsolete recovery command in current command tests.
- `tests/transactions/test_recovery.py` (7 tests): **rewrite** historic-ID adoption/recovered-record creation and session recovery expectations. Retain and adapt exact-retry, collision preservation, and late-stop/orphan evidence cases.
- `tests/transactions/test_recovery_diagnostics.py` (3 tests): **rewrite** adoption/attachment diagnostics; retain collision and unresolved evidence diagnostics, rename to evidence-focused test module.
- `tests/test_disconnected_status.py`: **rewrite** test that assumes persisted inferred stop; keep genuine late stop / disconnected status coverage with new semantics.

### Modify transaction identity and ingestion
- `tests/transactions/test_archive.py` (15 tests): **retain** exact retry across restart, failed-write behavior, charge-point/connector collision quarantine, pre-start evidence quarantine, normal matching continuation; **replace** recovered-ID allocator/origin tests with monotonic allocation and no SQLite reconciliation; add distinct starts one second apart, no stop, durable high-water mark, and exact-retry receipt evidence.
- `tests/session/test_session.py` (5 tests): **rewrite** unknown stop/meter recovery into orphan-only evidence, and SQLite-locked retry test into independent-of-SQLite start allocation. Keep raw-frame and outbound evidence tests.
- `tests/session/test_integration.py`: **extend** full WebSocket flow with repeated distinct starts, no intervening stop, late real stop, and exact retry.
- `tests/transactions/test_query.py` (11 tests): **replace** recovered-active/stopped transaction expectations with incomplete/unresolved evidence views and inferred effective boundaries. Keep filtering, ordering, malformed-file, and collision visibility.
- `tests/transactions/test_detail.py` (7 tests): **extend** observed vs next-start-inferred effective stop, unknown duration/energy, and late observed stop precedence. Preserve valid energy semantics.
- `tests/transactions/test_local_time.py` (5 tests): **retain and extend** charger timestamp vs CSMS receipt-time ordering for inferred boundary.
- `tests/cli/test_txn_explain.py` (3 tests): **extend** overlapping starts on same connector and inferred boundary provenance; don't equate absence of stop with confirmed ongoing physical charging.
- `tests/cli/test_inspection.py`: **review** read-only `reconcile` snapshot command independently from recovery worker; retain if it still provides supported inspection, or replace if removed as part of CLI simplification.
- `tests/cli/test_live_transactions.py`: **retain** live energy and unknown-energy checks; adapt open/last classification to new inferred boundary.
- `tests/cli/test_transactions.py`: **retain** CLI argument/JSON contract tests, extend effective-stop provenance and incomplete state.

### Preserve/extend cross-cutting contracts
- `tests/test_export_contract.py`, `tests/test_export_transaction_metadata.py`: preserve event cursor and source identity, ensure no derived stop becomes an exported synthetic OCPP event, ensure charge-point-scoped identity.
- `tests/test_schema.py`: preserve schema safety/upgrade coverage; do not create migration code for unused exporter.
- `tests/ocpp_csms/test_wss_transaction.py`: preserve WSS field-style transaction integration; add repeated start and reconnect behavior if feasible.
- `tests/rfid/test_session_authorization.py`: preserve permissive authorization and ensure multiple legitimate starts do not regress.
- `tests/test_status.py`, `tests/test_connector_status.py`, `tests/test_emergency_stop_status.py`, `tests/control/test_remote_commands.py`: audit stale-open assumptions when control guards are changed.
- `tests/test_operational_reports.py`, `tests/rfid/test_report.py`, `tests/test_export_transaction_metadata.py`: review grouping/energy/report output for scoped identity and inferred boundaries.
- `tests/evidence/test_store.py`, `tests/evidence/test_timeline_grouping.py`: retain actual frame persistence and event ordering; add duplicate receipt evidence as needed.
- `tests/ocpp_csms/test_deployment_profile.py`: distinguish appliance/deployment recovery from **transaction** recovery; do not delete unrelated cutover tests based only on their names.
- `tests/evidence/README.md`: remove guidance to preserve obsolete recovery tests forever; document new contract tests.

## First red-test batch (before implementation)
Create `tests/transactions/test_simplified_contract.py` and test:
1. Different start timestamps on same connector (one second apart) allocate distinct IDs, previous remains unmodified.
2. Identical start request retransmitted after restart and beyond 60 seconds returns original ID (without sleeping; use a controlled clock).
3. Same timestamp but different payload is a distinct start, with diagnostics.
4. Unknown MeterValues and StopTransaction preserve orphan evidence without creating recovered transaction.
5. Derived next-start boundary is exposed by query/report, never stored as StopTransaction.
6. Late actual stop supersedes inferred boundary in reporting.
7. Allocator has no per-ID reservation ledger/SQLite reconciliation, is durable across restart, and is independent of SQLite locks.

Split tests if needed to keep test setup minimal. Expected failures should identify missing contract behavior, not missing imports or flaky time-dependent assertions.

## Implementation order after red phase
A. Minimal monotonic durable allocator and exact-evidence retry index; remove 60-second cache, reservation ledger and SQLite ID reconciliation.
B. Evidence-only handlers; eliminate recovered/adopted synthetic transaction records.
C. Derived query/report boundaries; no historical record mutation.
D. Credible live connector control guards.
E. Remove transaction recovery worker, CLI, policy and deploy hooks; clean tests/docs.
F. Verify export/JSON, test refactor, all CI and review.

**Important:** The `reconcile` inspection CLI and `ocpp_discover/reconcile.py` may have unrelated read-only or deployment functions. Audit their behavior before removing them; the target is inferred transaction recovery, not every function named 'reconcile'.
