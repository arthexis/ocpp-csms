"""Deterministic, read-only interpretation of archived OCPP transaction evidence."""
from __future__ import annotations
from datetime import timedelta
from ocpp_csms.transactions.query import TransactionQuery
from ocpp_csms.evidence.diagnostics import transaction_events, events_between

def analyze_transaction(data_dir, transaction_id, *, context_minutes=0):
    view = TransactionQuery(data_dir).get(transaction_id)
    if view is None:
        raise ValueError(f"Transaction {transaction_id} not found")
    record = view.record
    rows = transaction_events(data_dir, transaction_id)
    actions = [str(row["action"]) for row in rows]
    findings = []
    def finding(code, severity, certainty, message):
        findings.append({"code": code, "severity": severity, "certainty": certainty, "message": message})
    started = isinstance(record.get("start"), dict)
    stopped = isinstance(record.get("stop"), dict)
    if not started:
        finding("missing_start", "warning", "observed", "No StartTransaction archive evidence")
    if stopped:
        finding("stop_recorded", "info", "observed", "StopTransaction is recorded")
    elif view.status == "disconnected":
        finding("inferred_stop", "warning", "inferred", "Transaction termination was inferred; no StopTransaction observed")
    else:
        finding("missing_stop", "warning", "observed", "No StopTransaction is recorded; charging outcome is unknown")
    if view.unresolved:
        finding("unresolved_evidence", "warning", "observed", f"{len(view.unresolved)} unresolved archive record(s)")
    if view.active and view.connector_id is not None:
        overlaps = sorted(other.transaction_id for other in TransactionQuery(data_dir).active(
            charger=view.charge_point_id, connector=view.connector_id
        ) if other.transaction_id != transaction_id)
        if overlaps:
            finding("overlapping_open_transactions", "warning", "observed",
                    f"Other open transactions on connector {view.connector_id}: {overlaps}")
    # Compare archive meterStart/meterStop only when both are recorded. A lower
    # meterStop is anomalous, but meter rollover or reset remains possible.
    start_payload, stop_payload = record.get("start"), record.get("stop")
    if isinstance(start_payload, dict) and isinstance(stop_payload, dict):
        first, last = start_payload.get("meter_start"), stop_payload.get("meter_stop")
        if isinstance(first, (int, float)) and not isinstance(first, bool) and isinstance(last, (int, float)) and not isinstance(last, bool) and last < first:
            finding("meter_decrease", "warning", "observed", "Stop meter value is lower than start; rollover or reset is possible")
    meters = record.get("meter_values")
    meter_count = len(meters) if isinstance(meters, list) else 0
    context = []
    if context_minutes:
        timestamp = view.received_activity_at
        start = (timestamp - timedelta(minutes=context_minutes)).isoformat()
        end = (timestamp + timedelta(minutes=context_minutes)).isoformat()
        context = [{"id": row["id"], "kind": row["kind"], "action": row["action"], "at": row["occurred_at"]}
                   for row in events_between(data_dir, charger_id=view.charge_point_id,
                                             since=start, until=end, limit=None)
                   if row["kind"] == "runtime" or row["transaction_id"] != transaction_id]
    return {"transaction_id": transaction_id, "cp": view.charge_point_id,
            "connector": view.connector_id, "rfid": view.id_tag, "state": view.status,
            "start_recorded": started, "stop_recorded": stopped,
            "meter_batches": meter_count,
            "events": [{"id": row["id"], "at": row["occurred_at"], "action": row["action"], "direction": row["direction"]}
                       for row in rows],
            "actions_observed": sorted(set(actions)), "findings": findings,
            "context": context,
            "disclaimer": "Observations are not proof of physical charging or why a stop was missing."}
