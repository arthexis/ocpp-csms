# R6C-3B1 — offline owner-epoch and call-UID feasibility

**Status:** TEST-ONLY / NO-GO. All code in tests/offline, using pytest tmp_path for disposable SQLite. No production schema, migration, endpoint, handler integration, service, credential, permission, lease, CleanVerified, admission, replay, reconnection, Ansible or deployment.

References: CSMS main 77452e39efb1702cc78c753724d1d30892ae411d (schema v8); simulator PR Draft #20 at 480dc0fce6195353646d572639a90af7d979b262, CI #43062051201 SUCCESS.

## What this prototype demonstrates

- Scope = (source_id, database_incarnation, tenant_id, cp_id).
- One test-owned SQLite authority row per scope maintains epoch, session, revision. BEGIN IMMEDIATE serializes owner changes and Start/Meter/Stop evidence commits; every commit checks the same epoch/session and advances revision. SQLite WAL is enabled only on the temporary test file.
- A stale WebSocket cannot commit Start/Meter/Stop. An in-flight write that commits before the new epoch is visible in the later owner snapshot; subsequent stale writes fail.
- Independent synthetic attempt+call UID registration is required before a simulated Start and prevents replays and ambiguous duplicate IDs. This registration is an ASSUMED TEST INPUT, not evidence that current Go can do it.
- Snapshot is consistent, observational, ALWAYS quarantined. Unknown Stop is orphan evidence, not closure. There is no CleanVerified or activate method.

## Critical real-library feasibility finding

Simulator pins github.com/lorenzodonini/ocpp-go **v0.19.0**. Its ocpp1.6/charge_point.go StartTransaction builds the OCPP payload and calls cp.SendRequest. The latter invokes ocppj.Client.SendRequest, which internally calls CreateCall (ocppj/client.go). CreateCall generates the UID with package-global messageIdGenerator (ocppj/ocppj.go), *after* the high-level StartTransaction call begins and without a pre-send per-attempt hook. StartTransaction's variadic props mutate only the request payload, not the envelope UID. SetMessageIdGenerator is package-global, unsafe as an implicit attempt binding under multiple concurrent callers, and provides no proof of durable pre-send reservation. Thus actual Go pre-send UID persistence is **NOT PROVEN** using the existing interfaces: all attempt resolutions remain AMBIGUOUS.

Python CSMS uses ocpp>=0.26,<1; its ChargePointSession._handle_call delegates to python-ocpp. Upstream python-ocpp exposes msg.unique_id as call_unique_id to explicitly declared handler parameters, but the present on_start_transaction(self, **payload) does not declare it, and EventStore.commit_start persists only exact canonical payload and CSMS transaction ID. The installed Python library version and capture hook require separate pinned offline integration tests before any production claim.

Sources:
- https://github.com/lorenzodonini/ocpp-go/blob/v0.19.0/ocpp1.6/charge_point.go
- https://github.com/lorenzodonini/ocpp-go/blob/v0.19.0/ocppj/client.go
- https://github.com/lorenzodonini/ocpp-go/blob/v0.19.0/ocppj/ocppj.go
- https://github.com/arthexis/ocpp-csms/blob/77452e39efb1702cc78c753724d1d30892ae411d/src/ocpp_csms/session/connection.py

## Negative matrix and remaining blockers

Stale Start/Meter/Stop; concurrent writer and new owner claim; unknown Stop; identical hypothetical Start payloads with distinct synthetic UID bindings; missing/repeated UID; stale epoch; cross-source/tenant/CP/incarnation; empty identity; snapshot without activation.

No authenticated source/tenant mapping, no real session fencing or handler modification, no distributed lease, no transport pre-send binding, no complete post-fence certificate, no revision-guarded activation, and no rollback proof. A restored SQLite database could rewind its own epoch and revision; real recovery requires an external monotonic witness or explicit post-restore invalidation.

**Future separately authorized R6C-3B2:** investigate a Go transport adapter that durably allocates/records a per-attempt UID *before* enqueue/send, and a pinned python-ocpp receiver capture test. Maintain simulator PR #20 Draft, BeforeAvailable Quarantined, R6 unchecked. Do not implement production owner state until both sides prove correlation and security.
