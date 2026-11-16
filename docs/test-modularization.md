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

## Stage G: split status presentation checks by behavior

Split the oversized `tests/status/test_status.py` at the exact boundary before `test_status_last_seen_omits_microseconds_and_aligns_long_ids`. Move exactly four complete test functions, **without editing their bodies or names**, into `tests/status/test_status_formatting.py`:

- `test_status_last_seen_omits_microseconds_and_aligns_long_ids`
- `test_timestamp_presentation_preserves_raw_evidence`
- `test_connected_duration_formats_seconds_minutes_hours_and_days`
- `test_status_table_displays_connection_age_not_boolean`

The retained original module tests derived status, connection liveness, schema reporting and filtering; the new module tests only presentation (timestamp, duration and table layout). Each moved test retains its own local imports. No shared helpers, fixtures, assertions, test parameters or monkeypatch targets are moved or changed. Both modules inherit `tests/conftest.py` with no new `__init__.py` or `conftest.py`.

**Acceptance:** compare the complete pytest collected node-ID multiset, normalizing only these four node IDs from `tests/status/test_status_formatting.py` back to their original `tests/status/test_status.py` prefix; run Python 3.11/3.13 CSMS+Discover and Ansible contract CI at the exact PR head, verify pass/skip counts and no new failures. Compare test bodies byte-for-byte. No production code, SQL, schema v8, export/v1, deployment, workflow or field operations. Keep #336 open and the PR Draft pending separate acceptance.

## Stage H: bounded duplicate-fixture audit and canonical CLI parser reuse

**Scope at baseline `aba0bdf0317c5212ce17091c661551a2e1671a35`:** inspect the existing four pytest configuration files (`tests/conftest.py`, `tests/cli/conftest.py`, `tests/control/conftest.py` and `tests/ocpp_discover/conftest.py`) and consolidate only the one demonstrably equivalent fixture override.

| Fixture / file | Finding | Decision |
| --- | --- | --- |
| `cli_parser` in `tests/conftest.py` and `tests/cli/conftest.py` | Both are default function-scoped and return the first result of the same `ocpp_csms.cli.build_parser()` call; the nested fixture shadows the root one in CLI tests | Remove **only** the nested duplicate and its now-unused import; let CLI tests inherit the root fixture |
| `parse_cli` and `profile_control` in `tests/cli/conftest.py` | CLI parsing wrapper and scoped patched control stub; no identical equivalent in the other inspected conftests | Keep unchanged, including fixture dependency on `cli_parser` |
| `record_runtime_event` in `tests/conftest.py` | Deliberate SQLite insert helper bound to `tmp_path`, not equivalent to parser/session fixtures | Keep unchanged; preserve transaction/DB authority semantics |
| `charge_point_session` in `tests/control/conftest.py` | Domain-specific session with archive, event store and fake transport; no equivalent fixture was found among the four conftests | Keep unchanged |
| `isolate_default_persistent_nftables` in `tests/ocpp_discover/conftest.py` | Autouse safety isolation, redirects nftables file effects to temporary paths and prevents host network state mutation; not a generic fixture | Keep unchanged, **never** move to the test root |

**Safety and scope:** fixture discovery for `tests/cli/` still walks up to `tests/conftest.py`. No other fixture names, declarations, scopes, autouse policies, test functions, IDs, assertions, mocks, parametrizations, imports in the tests, or runtime code change. The root canonical fixture still constructs a fresh parser per dependent test. Search of current repository tests shows no explicit use of `tests.cli.conftest` as an import path; the parser fixture remains accessible as `cli_parser`.

**Acceptance:** exact two-path diff (`tests/cli/conftest.py` and this roadmap), compare the count and names of collected pytest node IDs unchanged, the same pass/skip totals in Debian 12/Python 3.11, Debian 13/Python 3.13 and Ubuntu 24.04/Python 3.13, plus Discover and Ansible hermetic CI. CI must complete at exact PR head. No local/network/hardware testing and no deployment.

**Audit boundary:** this is a focused conftest-fixture audit, **not** a claim that all inline helpers across the complete test suite are duplicate-free. No parameterization is introduced: do not group cases on superficial similarity when their identity, event ordering, SQLite/transaction semantics or negative checks differ. Subsequent work may inspect module-local helpers with one independently reviewable change per proven equivalent group. Keep #336 OPEN and this change Draft until separately authorized acceptance.

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
