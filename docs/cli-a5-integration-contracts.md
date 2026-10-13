# CLI A5.3 — integration contract coverage

Tracking: #216. Builds on A5.1 #232 and A5.2 #233.

This slice adds entrypoint-level regression tests for canonical `txn start` and `txn stop` request construction with a global `--data-dir`, invalid control arguments and exit status 2, offline read-only `reserve ID --cp ... -j` selection, transaction JSON schema `ocpp-csms/transactions/v1`, and forwarder export schema `ocpp-csms/export/v1`. It also records the separate read-only transaction and control-operation parsers as an intentional current compatibility boundary.

No runtime/parser changes, no export cursor changes, and no changes to `mail`, `report`, or A4d maintenance commands. These tests complement rather than duplicate existing detailed command-unit tests.

A5.4 remains: run the full required CI matrix, inspect timing and integration coverage, reconcile #216 and confirm any justified compatibility exceptions. External consumers of `ocpp_csms.cli.parser` cannot be ruled out from in-repository search alone, so removal is out of scope.
