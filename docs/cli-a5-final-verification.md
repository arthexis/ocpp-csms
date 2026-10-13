# CLI A5.4 — final migration verification against #216

Baseline: A5.3 merged as #234 (`4c1dd52`). This report is a read-only verification artifact, not a parser or service change.

## Acceptance ledger

| #216 requirement | Evidence and disposition |
| --- | --- |
| Canonical command families and operator capability mapping | A1 historical matrix in `docs/cli-a1-command-contract.md`; current root registration in `src/ocpp_csms/cli/__init__.py`. `cp`/`cps`, `txn` query and operations, `reserve` list/detail/create/cancel, maintenance, diagnostics, `export`, `mail`, and `report` remain represented. No unrepresented capability confirmed in this pass. |
| Parser module / alias inventory | A1 matrix and A4d #227–#231 cover command organization; `src/ocpp_csms/cli/parser.py:add_transaction_command` remains exported but has no in-repository caller in code search. **Retain** until external consumers are assessed; do not infer safe removal. |
| Repository invocation migration | A5.1 #232 guard scans shell/YAML executable surfaces; A5.2 #233 corrected confirmed README examples. Manual review of `scripts/pr_simulator_smoke.py` and `scripts/persistent_simulator_check.py` finds the former invokes `ocpp-csms serve` and both invoke `ocpp-simulator run` (not deprecated transaction/reservation CLI). These scripts do not require migration. Dynamic callers outside the repository are unverified. |
| Stable machine integrations | `src/ocpp_forwarder/exporter.py:read_export` invokes `ocpp-csms --data-dir DIR export --after N --limit N --json`; schema remains `ocpp-csms/export/v1`. A5.3 #234 verifies schema registration and `ocpp-csms/transactions/v1` read schema. **Export cursor evolution belongs to #224/#226**, not A5. |
| Output, exit, and safety semantics | A4d contracts and A5.3 #234 cover canonical transaction control payloads, invalid requests (exit 2), read-only reservation ID JSON, and query/operation parser boundary. Existing `reserve` tests cover no charger query and exact-ID filtering; existing control/recovery tests remain the safety authority. |
| No OCPP/persistence change | A5.1–A5.4 are tests and documentation; no production module edited in this sequence. |
| CI / durations | A5.3 PR #234 CI run [#1336](https://github.com/arthexis/ocpp-csms/actions/runs/38450960749) completed successfully across Debian 12/Python 3.11, Debian 13/Python 3.13, Ubuntu 24.04/Python 3.13, Discover, CSMS, Ansible and final CI. **Fresh A5.4 CI must pass before merging this report.** Per-test duration measurements and any new performance regression are not independently established by the run summary. |

## Residual boundaries (not claims of completion)

1. **External caller audit:** public `ocpp_csms.cli.parser` facade may be used outside the repository. Keep it; deprecation requires an explicit consumer decision.
2. **Dynamic invocation detection:** A5.1's regex scans literal shell commands in `scripts/`, `ansible/`, `.github/workflows/`; it is not an AST parser for Python subprocess arrays or a scan of third-party deployment repos. The identified in-repo simulator scripts are compatible, and the Forwarder command is canonical.
3. **CLI special parsing:** `txn` operations are real subparsers selected by dispatch before the read-only `txn [ID]` parser; `reserve ID` is extracted before argparse. Both are tested. This is an intentional compatibility design, not proof of a remaining rewrite bug.
4. **End-to-end physical integration:** full device/deployment verification is separate from CLI parser acceptance; no physical charger was exercised in A5.
5. **#224/#226:** export transaction-change cursor and related PR remain separate; this report does not certify or modify that work.

## Closure recommendation

The repository-local CLI A1–A5 migration is **ready for acceptance review** if A5.4 CI is green. Close the CLI migration track only with explicit acknowledgment of external-caller and runtime integration boundaries above. Do not declare #224, #226, or live field tests complete by implication. Preserve all compatibility entry points until a separately justified removal.
