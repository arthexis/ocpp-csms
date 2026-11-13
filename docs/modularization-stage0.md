# Stage 0: modularization dependency and acceptance baseline

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
3. Export: isolate event, energy and snapshot projections with **one SQLite read snapshot**; preserve exact `ocpp-csms/export/v1` fields, ordered event IDs, exclusive `after`, `next`, `more`, source identity and transaction provenance. Reports must continue consuming the command JSON rather than opening CSMS SQLite.
4. Firmware and TLS: remove firmware's dependency on CLI-private helpers before moving domain services. Preserve authorization, reject semantics, certificate checks and WSS drain.
5. Storage, optionally Discovery: only with additional isolated installer/rollback validation. Preserve schema 8, WAL, backup, source ID, existing release and service interfaces. No separate repository.

### Acceptance for each future stage

- No public import regression (including `from package import module`, private symbols currently used by tests, and monkeypatch binding semantics).
- Offline wheels and both entrypoints remain valid on CI environments.
- No JSON schema, CLI, OCPP wire, or database behavior change; test JSON cursor replay, delayed StopTransaction, orphan evidence, failures and status provenance.
- Complete CI for CSMS/Discover on Python 3.11 and 3.13, with wheel smoke and no live node access.
- One small draft PR per stage; never merge/Ready/deploy without explicit authorization.

## Known limits of Stage 0

The new tests are representative baselines, **not** a complete external API inventory or golden snapshot of all JSON responses. In particular, the wheel check skips when `setuptools.build_meta` is absent from the pytest interpreter (the CI editable install uses build isolation). Installed distribution entrypoint metadata is always tested, and installed command smokes are part of current CI. A mandatory wheel build in an explicitly provisioned packaging environment remains a separate gate. Treat those as separate complementary gates, and strengthen the baseline before higher-risk moves.


## 2026-11-13: synchronization and extended characterization under #346

The historical original baseline above is retained for provenance. The Stage-0 Draft branch
is synchronized with main `2c400e8b3744881003eec1735be29de8e35981bd` by an explicit two-parent
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
  and imports both distributions from the isolated installation.

**Outstanding Stage-0 acceptance:** Python 3.13 jobs historically skipped the
backend-dependent wheel test, while Python 3.11 executed it. Those skips must
not be counted as wheel-build verification. The editable-install and installed
script metadata/CLI smokes in CI are separate coverage. A future explicitly
authorized CI/packaging prerequisite change is necessary if mandatory native
PEP 517 wheel build/install must execute on every supported runner, unless the
current runner provides a suitable offline build backend. Do not add downloads
or unbounded network access to make the test pass.

The Stage-1 source-to-caller classification and external-usage uncertainties are
maintained in [#346](https://github.com/arthexis/ocpp-csms/issues/346),
not promoted to removals in the Stage-0 tests/docs PR.
