# Test suite modularization roadmap — issue #336

## Stage A: export domain grouping

Move four test modules from the top-level `tests/` directory into `tests/export/` **without changing their contents**:

| Original | New path |
| --- | --- |
| `tests/test_export_contract.py` | `tests/export/test_export_contract.py` |
| `tests/test_export_cursor_semantics.py` | `tests/export/test_export_cursor_semantics.py` |
| `tests/test_export_status_provenance.py` | `tests/export/test_export_status_provenance.py` |
| `tests/test_export_transaction_metadata.py` | `tests/export/test_export_transaction_metadata.py` |

The top-level `tests/conftest.py` still applies to the relocated tests. No `tests/export/__init__.py` is added, so pytest path collection and normal module import naming stay consistent with existing non-package tests. No fixture or monkeypatch implementation is modified.

## Stage B: status and energy domain grouping

Four additional modules are relocated byte-for-byte in PR stage B, independently of PR #335:

| Original | New path |
| --- | --- |
| `tests/test_disconnected_status.py` | `tests/status/test_disconnected_status.py` |
| `tests/test_connector_status.py` | `tests/status/test_connector_status.py` |
| `tests/test_live_energy_export_metadata.py` | `tests/energy/test_live_energy_export_metadata.py` |
| `tests/test_transaction_soc.py` | `tests/energy/test_transaction_soc.py` |

No `__init__.py` or nested conftest is introduced: `tests/conftest.py` remains in scope. Verify exact Git blob equality, all normalized pytest node IDs and pass/skip totals versus the current `main` commit `59808b56411b300a3485e2f8fe365a48c76bf8df` for each CI environment. No production or test-body edits.

## Stage C: remaining root status and connector-query tests

Move exactly these three modules byte-for-byte to `tests/status/`:

| Original | New path |
| --- | --- |
| `tests/test_status.py` | `tests/status/test_status.py` |
| `tests/test_emergency_stop_status.py` | `tests/status/test_emergency_stop_status.py` |
| `tests/test_connector_query.py` | `tests/status/test_connector_query.py` |

The existing `tests/energy/` suite is unchanged in Stage C; all three moves cover status reporting, emergency-stop status and physical connector queries. The old `tests/conftest.py` remains the only applicable shared fixture file. No new `__init__.py`, `conftest.py`, monkeypatch hooks or test bodies are introduced. Compare exact Git blobs and normalize only the three path prefixes in CI pytest node IDs against `main` `bc54acac726e2ff2007108546cdcd3e8b5a0550f`.

## Stage D: storage/schema test grouping

Three modules are relocated byte-for-byte into `tests/storage/`, with no test-body, fixture or monkeypatch changes:

| Original | New path |
| --- | --- |
| `tests/test_schema.py` | `tests/storage/test_schema.py` |
| `tests/test_schema_storage.py` | `tests/storage/test_schema_storage.py` |
| `tests/test_persistent_wal.py` | `tests/storage/test_persistent_wal.py` |

Their imports are absolute, fixtures still inherit from `tests/conftest.py`, and existing monkeypatch targets stay in `ocpp_csms` modules. No `tests/storage/__init__.py` or nested conftest is created. Preserve current SQLite schema v8, WAL and failure assertions. Verify exact Git blobs, normalized pytest node IDs and pass/skip counts against `main` `941bc77b44adb3b5f4a678c43bad1b1a7e6093fd` across Python 3.11/3.13.

`tests/test_export_revision_fingerprint.py` is deliberately **not** moved: it resolves `src/ocpp_csms/export_contract.py` using `Path(__file__).resolve().parents[1]` and would fail if moved byte-identically into a nested directory. Any relocation requires a separately authorized behavioral review, not this stage.

## Stage E: node identity and configuration report tests

Relocate exactly two small modules byte-identically:

| Original | New path |
| --- | --- |
| `tests/test_node_identity.py` | `tests/storage/test_node_identity.py` |
| `tests/test_config_report.py` | `tests/status/test_config_report.py` |

Both destination directories are non-package suites with no nested `conftest.py`; root `tests/conftest.py` remains applicable. All source imports and monkeypatch targets are unchanged, including hostname patches under `ocpp_csms.node_identity`. Verify blob identity, normalized pytest node IDs and pass/skip counts for the exact main baseline `6b01aa75746cd7a26f94309b70aa768dbc47cc11` on Python 3.11/3.13. No fixture, assertion, schema, production, security or export behavior changes.

Explicit exclusions: `test_export_revision_fingerprint.py`, `test_deployment_preflight.py` and `test_release_prune_lock.py` use location-sensitive `__file__` paths; Evidence tests were not relocated because `tests/evidence/__init__.py` would alter test module import identities.

## Stage F: storage verification and nominal rating tests

Two additional root-level tests are relocated **byte-for-byte**, without changing their assertions, helpers, imports, fixtures, or monkeypatch targets:

| Original | New path |
| --- | --- |
| `tests/test_deployment_schema_verify.py` | `tests/storage/test_deployment_schema_verify.py` |
| `tests/test_charger_ratings.py` | `tests/energy/test_charger_ratings.py` |

The schema verification suite covers fail-closed schema/WAL checks and transaction/export identity preservation; the charger-ratings suite covers nominal rating metadata and its export representation, **not** measured power or charging control. These are categorization changes only. Both destination directories are non-package test directories without nested `conftest.py`, so root `tests/conftest.py` remains in scope; no changes to `ocpp_csms` source, SQLite v8, `ocpp-csms/export/v1`, or test code. The CI workflow continues to collect `tests/` recursively. Compare exact Git blobs, full pytest node-ID multisets with only these two path prefixes normalized, and pass/skip counts against the exact `main` baseline `80e257aec446fb08db0bae309a80a48ac3366b71` under Python 3.11/3.13. Keep #334/#335 independent and #336 open; PR remains Draft until separately authorized.

## Proposed follow-up stages (not implemented)

| Domain | Current test locations | Main risk | Acceptance |
| --- | --- | --- | --- |
| status | `tests/status/test_status.py`, `tests/status/test_emergency_stop_status.py`, `tests/status/test_connector_query.py`, other `tests/status/` modules and CLI status tests | fixtures and liveness vs historical observation | same collected node IDs, statuses and formatting assertions |
| energy | `tests/energy/test_live_energy_export_metadata.py`, `tests/energy/test_transaction_soc.py`, `tests/energy/test_charger_ratings.py`, `tests/transactions/` | shared events, source identity, timestamps and units | no changes to SoC/energy normalization or event provenance |
| export | remaining root `test_export_*.py`, export CLI and evidence suites | shared snapshot consistency, cursor semantics and private monkeypatch targets | preserve `ocpp-csms/export/v1` and authoritative SQLite |
| storage | `tests/storage/test_schema*.py`, `tests/storage/test_persistent_wal.py`, `tests/storage/test_deployment_schema_verify.py` | fixture scope, SQLite lifecycle and rollback evidence | schema v8 and WAL/backup semantics unchanged |
| tls | `tests/ocpp_csms/test_tls*.py`, `tests/ocpp_csms/test_basic_auth.py` | listener and authentication safety | full negative/auth/drain tests unchanged |
| firmware | `tests/cli/test_firmware*.py`, diagnostics suites | private CLI monkeypatch paths and transport | preserve preflight, URL validation and response handling |
| discovery | `tests/ocpp_discover/` (already grouped) | network mocking and installer coupling | leave grouped; no extra repo |

## Required verification

- All moved modules remain byte-identical; no test cases, assertions, fixture scopes or parametrizations are removed.
- CI continues to invoke `pytest tests` (including `tests/export`) and `pytest tests/ocpp_discover` in supported environments; no workflow changes.
- Compare exact collected test node IDs after normalizing only relocated path prefixes and compare pass/skip totals against the unchanged main baseline.
- Keep Stage 0 issue #334 and PR #335 independent. Test refactor stays Draft until explicitly accepted.
- No production code, SQLite schema v8, export/v1 behavior, credentials, systemd, Ansible operational execution or deployment changes.
