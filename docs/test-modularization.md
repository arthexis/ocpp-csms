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

## Proposed follow-up stages (not implemented)

| Domain | Current test locations | Main risk | Acceptance |
| --- | --- | --- | --- |
| status | `tests/test_status.py`, `tests/test_connector_status.py`, `tests/test_disconnected_status.py`, CLI status tests | fixtures and liveness vs historical observation | same collected node IDs, statuses and formatting assertions |
| energy | `tests/test_live_energy_export_metadata.py`, `tests/test_transaction_soc.py`, `tests/transactions/` | shared events, source identity, timestamps and units | no changes to SoC/energy normalization or event provenance |
| export | remaining root `test_export_*.py`, export CLI and evidence suites | shared snapshot consistency, cursor semantics and private monkeypatch targets | preserve `ocpp-csms/export/v1` and authoritative SQLite |
| storage | `tests/test_schema*.py`, `tests/test_persistent_wal.py`, `tests/test_deployment_schema_verify.py` | fixture scope, SQLite lifecycle and rollback evidence | schema v8 and WAL/backup semantics unchanged |
| tls | `tests/ocpp_csms/test_tls*.py`, `tests/ocpp_csms/test_basic_auth.py` | listener and authentication safety | full negative/auth/drain tests unchanged |
| firmware | `tests/cli/test_firmware*.py`, diagnostics suites | private CLI monkeypatch paths and transport | preserve preflight, URL validation and response handling |
| discovery | `tests/ocpp_discover/` (already grouped) | network mocking and installer coupling | leave grouped; no extra repo |

## Required verification

- All moved modules remain byte-identical; no test cases, assertions, fixture scopes or parametrizations are removed.
- CI continues to invoke `pytest tests` (including `tests/export`) and `pytest tests/ocpp_discover` in supported environments; no workflow changes.
- Compare exact collected test node IDs after normalizing only relocated path prefixes and compare pass/skip totals against the unchanged main baseline.
- Keep Stage 0 issue #334 and PR #335 independent. Test refactor stays Draft until explicitly accepted.
- No production code, SQLite schema v8, export/v1 behavior, credentials, systemd, Ansible operational execution or deployment changes.
