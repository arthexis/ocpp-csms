# Evidence test organization

- `test_store.py`: SQLite event and transaction evidence persistence.
- `test_diagnostics.py`: event collection, explanation, and diagnostics formatting.
- `test_timeline_cli.py`: CLI output modes, JSON contracts, filtering, and formatting integration.
- `test_timeline_grouping.py`: pure human-readable grouping rules and edge cases.

Keep CLI integration and pure grouping tests separate: their failure boundaries and fixtures differ.

## Phase 1 tombstone review

- Keep `tests/control/test_evidence.py`: exercises control-to-evidence integration rather than standalone event storage.
- Keep `tests/cli/test_diagnostics.py`: verifies CLI dispatch and argument handling, not just evidence formatters.
- Keep `tests/test_recovery_diagnostics.py`: protects recovery evidence and incident diagnosis.
- Preserve historic transaction archive/recovery tests; prior formats remain operational data.
- No verified tombstones deleted in this phase. Do not remove legacy or failure-path regression coverage based solely on its name.

Follow-up: only merge tests when duplicated assertions have been verified against the same public behavior; avoid mechanically merging CLI and pure-function suites.
