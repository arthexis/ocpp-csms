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
