# Stage 0: modularization dependency and acceptance baseline

## Final administrative status (2026-11-13)

Stage 0 was accepted and closed in [issue #334](https://github.com/arthexis/ocpp-csms/issues/334); its tests/docs-only [PR #335](https://github.com/arthexis/ocpp-csms/pull/335) was merged. The subsequent Stage 1A/1B audit ([PR #347](https://github.com/arthexis/ocpp-csms/pull/347), [PR #348](https://github.com/arthexis/ocpp-csms/pull/348)) was integrated, and all eight Stage 1 checks in [#346](https://github.com/arthexis/ocpp-csms/issues/346) were accepted **administratively as audited/classified**, not as passed deletion gates. Issue #346 remains open for Stages 2–6; #336 remains open and independent. No production-code removal, behavior change, deployment, or field verification was authorized. The historical snapshots below are retained for provenance.

The Python 3.13 wheel build/install remains **NOT TESTED** where skipped; it is a separate fail-closed prerequisite only before a deployment targeting Python 3.13. Unknown installed, private, and dynamic consumers remain unknown, and the Stage 1 negative gates must be met before any future removal.

Baseline: `main` 77452e39efb1702cc78c753724d1d30892ae411d. This is documentation and characterization tests only. SQLite schema 8, security, deployment and all CLI/JSON behavior remain unchanged.

## Precise direct-import edges (observed on baseline)

| Candidate | Direct dependencies / callers | Compatibility surface | Risk |
| --- | --- | --- | --- |
| status | `status.py -> evidence.store, schema, runtime, rfid.authorization`; `status_contract -> status`; consumers in CLI, reports, export, install_cutover, firmware | `ocpp_csms.status` symbols, `ocpp_csms.status_contract`, `ocpp-csms/status/v1`, presentation and connection interpretation | medium |
| energy | `energy_query -> schema`; `energy_contract -> energy_query, output`; `export_contract -> energy_query._entry_sample`; `cli.energy -> EnergyQuery` | `EnergyQuery`, `EnergySample`, `_entry_sample` as current patch seam, `ocpp-csms/energy/v1` | medium |
| export | `export_contract -> energy_query._entry_sample, evidence.contracts, output, schema, status, status_contract, charger_ratings, transactions.contracts, transactions.query, transactions.export_metadata`; `cli.export -> export_contract` | `ocpp_csms.export_contract`, `ocpp-csms/export/v1`, cursor and snapshot provenance | high |
| storage | `schema -> node_identity`; `schema_storage -> schema`; `backup_archive -> schema`; `persistent_wal` has no internal import; `install_cutover, evidence.store, transactions.query, ocpp_discover` consume schema | schema 8, `DATABASE_FILENAME`, `source_id`, WAL, recovery, untouched SQLite identity | critical |
| tls | `tls_listener -> tls_config, websockets`; `server -> tls_listener, auth`; CLI and tests reference config/auth | TLSConfig, TLSListener, auth checks, listener drain, no relaxation of trust boundary | high |
| firmware/diagnostics | `firmware_preflight -> cli.inspection._features/_reconciliation, control, evidence.diagnostics, status, transactions.query`; `diagnose_receiver -> diagnostic_upload.ROOT/TOKEN_RE` | preflight decisions, uploaded diagnostic token flow, private monkeypatch seams | high |
| discover (separate distribution?) | `ocpp_discover.service/handoff -> ocpp_csms.install_cutover`; `handoff/first_contact -> ocpp_csms.schema` | `ocpp-discover` console command and systemd runtime | high; defer |

This is a *direct-import* matrix, not proof that every lazy/dynamic import or external downstream consumer is enumerated. No code move has occurred.

## Stage ordering and gates

0. Characterization: `tests/test_modularization_baseline.py` checks legacy imports and identity aliases, current monkeypatch seams, offline CLI/export JSON, SQLite-backed cursor behavior and installed console-script metadata; an offline wheel build runs only where the packaging backend is already installed. The existing CLI, export cursor, SQLite authority, TLS, firmware and Discover tests remain mandatory.
1. Energy: move parsing/query/contract internals, preserving `ocpp_csms.energy_query`, `ocpp_csms.energy_contract` and the export sample semantics; verify units, UTC normalization, invalid samples and replay.
2. Status: isolate models/query/formatting; retain legacy `ocpp_csms.status` import path, return types and `status/v1`.
3. Export: isolate event, energy and snapshot projections with **one SQLite read snapshot**; preserve exact `ocpp-csms/export/v1` fields, ordered event IDs, exclusive `after`, `next`, `more`, source identity and transaction provenance. Reports' normal ingestion consumes the `ocpp-csms/export/v1` producer contract (as JSON pages or the existing `export_contract` callable); **separate, narrowly scoped Reports maintenance and WAL helpers already read CSMS SQLite in read-only mode**. Preserve those consumers and their documented authority boundaries; do not introduce new database writes or break schema v8.
4. Firmware and TLS: remove firmware's dependency on CLI-private helpers before moving domain services. Preserve authorization, reject semantics, certificate checks and WSS drain.
5. Storage, optionally Discovery: only with additional isolated installer/rollback validation. Preserve schema 8, WAL, backup, source ID, existing release and service interfaces. No separate repository.

### Acceptance for each future stage

- No public import regression (including `from package import module`, private symbols currently used by tests, and monkeypatch binding semantics).
- The actual application wheel must build/install offline and both installed wheel entrypoints must pass `--help` in the representative Debian 12 / Python 3.11 packaging environment. Python 3.13 packaging is a separate prerequisite **only before Python 3.13 deployment**, not a Stage-0 blocker; distinguish wheel-test `SKIPPED` from `PASSED`.
- No JSON schema, CLI, OCPP wire, or database behavior change; test JSON cursor replay, delayed StopTransaction, orphan evidence, failures and status provenance.
- Complete functional CI for CSMS/Discover on Python 3.11 and 3.13, with editable-install/CLI smoke on both and the real wheel smoke on representative Python 3.11; no live node access or workflow changes.
- One small draft PR per stage; never merge/Ready/deploy without explicit authorization.

## Known limits of Stage 0

The new tests are representative baselines, **not** a complete external API inventory or golden snapshot of all JSON responses. In particular, the wheel check skips when `setuptools.build_meta` is absent from the pytest interpreter (the CI editable install uses build isolation). Installed distribution entrypoint metadata is always tested, and installed command smokes are part of current CI. The representative Debian 12 / Python 3.11 CI already built and installed the real application wheel. The Python 3.13 build test is explicitly **NOT TESTED** when skipped and remains a fail-closed pre-deployment requirement if Python 3.13 becomes a field target, not a refactor gate. Keep these independent coverage claims separate before higher-risk moves.


## 2026-11-13: synchronization and extended characterization under #346

The historical original baseline above is retained for provenance. At synchronization time,
the Stage-0 Draft branch was synchronized with main `2c400e8b3744881003eec1735be29de8e35981bd` by an explicit two-parent
merge (first parent: original Draft HEAD; second parent: reviewed main HEAD).
The merge tree contains the complete main snapshot plus only the two Stage-0
tests/docs paths; do not undo the independently integrated test-suite relocations.

New offline characterization:
- Import/reexport interfaces: `ocpp_csms.cli.parser`, `ocpp_csms.contracts`, `ocpp_csms.control`,
  and the preserved test patch seam on `control.os`.
- A non-listening TLS status query records that the *static* placeholder
  `not_implemented` and the *live* listener state `stopped` are different contracts.
  This is characterization only, not approval to change a response.
- The entrypoint dispatches historical `txn stop`, `txn explain`, `txn [ID] --json`,
  and `reserve [ID] --json` via fully stubbed functions, without a control socket.
- Cursor pagination/replay and orphan StopTransaction remain SQLite-backed.
- Where the PEP 517 setuptools backend is **already installed**, the wheel smoke
  now additionally installs the real wheel into a disposable `pip --target`
  directory with `--no-index --no-deps`, checks generated console scripts,
  and imports both distributions from the isolated installation. It also runs each generated wheel script directly with `--help`, isolated via `PYTHONPATH` to that installation.

**Stage-0 packaging acceptance (decision #334/#346, 2026-11-13):** Debian
12 / Python 3.11 is the representative installed-wheel gate, now including
the wheel's own generated script `--help` checks. Python 3.11 and 3.13 must
both pass functional CSMS/Discover imports, editable-installed commands,
CLI/JSON, evidence/SQLite and cursor tests. When the PEP 517 backend is absent
in the Python 3.13 test interpreter, the real wheel test is `SKIPPED` and its
packaging status is **NOT TESTED**, not failure and not acceptance. Actual wheel
build/install and entrypoint smoke on Python 3.13 are mandatory **only before
an explicitly authorized deployment onto Python 3.13**, in a platform-matched
isolated acceptance. Do not change CI, Ansible or package metadata to force
extra wheel builds for Stage 0.

## Confirmed downstream consumers and limits of this baseline

The source audit under [#346](https://github.com/arthexis/ocpp-csms/issues/346)
found concrete, version-sensitive cross-repository consumers beyond ordinary
Python imports within CSMS:

- **OCPP Reports, interpreter-based Python calls:** In
  [`active_csms.py`](https://github.com/arthexis/ocpp-reports/blob/main/src/ocpp_reports/active_csms.py),
  Reports discovers the installed CSMS service executable, its Python shebang,
  and the active schema-8 data directory. It runs that CSMS interpreter using
  `python -c` strings that import `ocpp_csms.cli.cli_data_dir`,
  `ocpp_csms.export_contract.export_contract` and
  `ocpp_csms.transaction_soc.transaction_soc`. In
  [`follow.py`](https://github.com/arthexis/ocpp-reports/blob/main/src/ocpp_reports/follow.py),
  the local-producer fallback lazily imports `export_contract` directly.
  These exact paths, signatures, JSON schemas (`export/v1` and
  `transaction-soc/v1`), source identities and cursors are compatibility
  contracts even when standard source-import searches miss the subprocess calls.
- **OCPP Reports, deliberately bounded SQLite consumers:**
  [`csms_wal_reader.py`](https://github.com/arthexis/ocpp-reports/blob/main/src/ocpp_reports/csms_wal_reader.py)
  opens the active `ocpp-csms.sqlite3` using SQLite `mode=ro`, keeping
  WAL/SHM sidecars available without holding a long read transaction.
  [`source_activation_plan.py`](https://github.com/arthexis/ocpp-reports/blob/main/src/ocpp_reports/source_activation_plan.py)
  and [`source_preflight.py`](https://github.com/arthexis/ocpp-reports/blob/main/src/ocpp_reports/source_preflight.py)
  inspect `appliance_metadata.source_id`, `events`, `transactions`,
  schema-version metadata and coordinated **read-only** snapshots in
  scoped maintenance paths. These are real consumers of SQLite v8, filenames,
  source identity and WAL behavior, not approval for arbitrary Reports writes
  to the live CSMS database.
- **GWay Remote:** [`csms_health.py`](https://github.com/arthexis/gway-remote/blob/main/gway_remote/csms_health.py)
  runs `ocpp-csms status --json` and validates `ocpp-csms/status/v1`.
  Its mobile command dispatcher also consumes historical CLI commands. Preserve
  observable status fields, grammar, exit codes and script installation paths.
- **OCPP Simulator:** the R6 Go spike reads synthetic, disposable CSMS SQLite
  v8 evidence for **offline observation only**. It is not a runtime recovery
  approval. Preserve the v8 evidence contract and never infer transaction
  completion or simulator permission from a read-only snapshot.

This inventory does **not** prove that no other dynamically loaded, third-party
or field-installed consumer exists. Stage 1 subsequently classified these tombstones
without deleting code; any newly authorized refactor must preserve these
interfaces or propose a separately reviewed contract change.

The Stage-1 source-to-caller classification and external-usage uncertainties are
maintained in [#346](https://github.com/arthexis/ocpp-csms/issues/346),
not promoted to removals in the Stage-0 tests/docs PR.
