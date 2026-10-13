# CLI A1 — canonical command contract and CI migration plan

Tracking: #216. **Historical A1 design baseline:** this document originally defined the intended A2–A5 migration, not the present CLI. For the shipped command grammar, use `ocpp-csms --help` and the README. A4d #227–#231 and A5.1 #232 are merged; A5.2 reconciles operator examples while later A5 slices complete integration verification.

## Principles

1. Minimize **keystrokes per useful operation** for high-latency remote terminals. Favor bare resource listings and positional primary IDs.
2. One canonical spelling for each operation; retire duplicate aliases after auditing callers. A compatibility shim, if needed for automation, must be temporary and tested.
3. Never shorten away safety confirmation, scheduling, scope, or transaction identity.
4. Local inspection remains usable offline; no reporting or network service required.
5. Machine-readable JSON schemas and export cursors must not change accidentally.

## Command migration matrix

| Intent | Historical A1 entry points | Canonical target (verify with `--help`) | Side effect | Migration/CI |
| --- | --- | --- | --- | --- |
| Appliance status | `status` | `status` | read | preserve status JSON contract |
| One charge point | `charger ID`, `cp ID`, `status ID` | `cp ID` (status ID may be removed after audit) | read | update alias tests |
| Charge point list | `chargers`, `cps` | `cps` | read | update aliases and docs |
| Transaction list | `transactions`, `transaction`, `txns`, `txn` | `txn` | read | update transaction parser and JSON tests |
| Transaction detail | `txn ID` | `txn ID` | read | preserve event/timestamp semantics |
| Transaction start | `start`, `txn start` via argv rewrite | `txn start` | charger control | retain timing/authorization defaults; remove rewrite |
| Transaction stop | `stop --txn ID`, `txn stop ID` via rewrite | `txn stop ID` | charger control | retain timing/transaction checks |
| Transaction explain | `txn explain ID` via special parser | `txn explain ID` | read | real subparser, preserve evidence |
| Recovery | `recover`, `transactions recover` via rewrite | `txn recover` | potentially mutating | preserve dry-run and safeguards |
| Reservation list | `reservation list` | `reserve` | read | preserve evidence semantics: requested != active |
| Reservation detail | not implemented | `reserve ID` | read | new exact-ID evidence query and tests |
| Reservation create | `reserve ...`, `reservation create ...` | `reserve create ...` | charger control | preserve expiry, ID, connector validation |
| Reservation cancel | `reservation cancel ID` | `reserve cancel ID` | charger control | preserve response semantics |
| Event timeline | `events` | `events` | read | shared time/limit parser |
| Alert timeline | `alerts` | `alerts` | read | shared time/limit parser; classify before count limit |
| Time-window explanation | `explain CP` | `explain CP` | read | distinct from txn explain |
| Charger inspection | `inspect` | `inspect` pending A4 | read + optional safe live query | offline mode retained |
| Capability inventory | `capabilities` | pending A4 | safe live query | no conflation with passive evidence |
| Reconciliation | `reconcile` | pending A4 | read | never close transactions from status alone |
| Charger diagnostics upload | `diagnostics request/history` | retain pending A4 | charger control/read | separate from inspect |
| Firmware | `firmware update/check/status/history` | retain | charger control/read | `--confirm` and scheduling mandatory |
| Smart charging | `profile ...` | retain pending A4 | charger control/read | preserve profile semantics |
| RFID | `rfid ...` | retain pending A4 | charger control/read | preserve local list/cache distinction |
| Configuration | `config ...` | retain pending A4 | charger control/read | preserve sensitive-value masking |
| Maintenance | `trigger`, `availability`, `unlock`, `reset` | retain pending A4 | charger control | preserve guardrails |
| Local data reset | `data reset` | retain pending production-surface review | destructive | dry-run default; `--now` explicit |
| Energy telemetry | `energy` | `energy` | read | stable JSON export |
| Forwarder export | `export` | `export` | read | **must preserve cursor JSON contract** |
| Operational report | `report` | retain until ocpp-reports extraction | read | no removal in A |
| Email | `mail ...` | retain until ocpp-reports extraction | external delivery | no removal in A |
| TLS and auth | `tls ...`, `auth ...` | retain | security config/control | preserve existing checks |
| Vendor extension | `data-transfer send` | retain | charger control | preserve vendor/data validation |
| Help | `help [topic]`, `--help` | choose after test audit | read | avoid printing all commands by default |

## Argument conventions

- `--cp`: select a charge point; omit only if target unambiguous.
- `-c`: physical connector ID; keep connector 0 meaning only where OCPP explicitly allows it.
- `-j`: JSON; `-n`: count limit; `--since`/`--until`: timestamps; `--at`: point-in-time inspection.
- Positional ID when it is the principal resource (`cp ID`, `txn ID`, `reserve ID`).
- Short names do not imply automatic execution: existing `--now`, `--confirm`, scheduling and dry-run rules stay in place.

## Historical CI impact inventory (A1 baseline; not a current outstanding-work list)

At A1 planning time, the following files encoded **old CLI spellings**. Several have since been migrated; do not treat this list as confirmed outstanding work:
- `tests/cli/test_txn_control_alias.py`: argv rewriting and standalone start/stop behavior.
- `tests/cli/test_command_contract_matrix.py`: `reserve` creation and `reservation create/cancel`.
- `tests/cli/test_json_contracts.py`: `transactions --json` and `reservation list`.
- `src/ocpp_csms/cli/__init__.py`: manual transaction argv rewriting and root dispatch.
- `src/ocpp_csms/cli/transactions.py`, `chargers.py`, `reservation.py`: aliases and parser registrations.
- `src/ocpp_csms/cli/parser.py`: partial parser facade; assess consumers before deleting.
- `README.md`: current operator examples.

Additional searches must cover all `tests/`, `scripts/`, `ansible/`, `docs/` and workflows for executable CLI calls, not merely text references. This is an identified **remaining audit**, not a claim of complete coverage.

## CI policy for A2–A5

1. **Baseline first:** record required GitHub checks, job matrix, and durations on main; verify existing pytest timing configuration (`--durations=10`, `pytest-timeout`) remains enabled.
2. **Change parser + tests atomically:** each PR updates canonical command fixtures, JSON-contract tests, help/docs examples, and script/Ansible call sites touched by that change.
3. **Fast CLI gate:** run focused parser, CLI, control, reservation, and JSON contract tests before full CI; preserve a single authoritative required check if the repository uses one.
4. **Full CI before merge:** run existing Python matrix, integration and deployment validations. Never weaken required checks to make a CLI rename pass.
5. **No runtime regressions:** maintain exact JSON schema names, exit codes, safety defaults, and remote-control payload semantics unless a separately reviewed change explicitly requires otherwise.
6. **Migration check:** search for removed spellings and fail on stale executable invocations, but permit intentional historical documentation or migration tests.
7. **Track duration:** retain pytest duration reports; investigate new slow tests or increased overall CI time.

## Historical suggested PR slices

- **A1 (this PR):** command matrix and CI migration policy; no runtime changes.
- **A2a:** `cp`/`cps` and `txn` parser/subcommands + matching tests.
- **A2b:** `reserve` grammar and new read-only reservation-ID detail + matching tests.
- **A3:** shared time/count/JSON parsing and output; dedicated test parametrization.
- **A4:** inspection/maintenance review, remove only proven redundant entry points.
- **A5:** repository-wide stale-invocation scan, tests/docs cleanup, matrix and CI hardening.

## Definition of done for A1

The A1 contract and its originally identified CI hotspots were documented. This was the **historical A1 exit criterion**, not a current instruction to restart A2. See `docs/cli-a5-invocation-audit.md` for the current migration verification inventory.
