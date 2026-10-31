"""Read-only, bounded SoC evidence for an existing CSMS transaction.

Unlike cursor export/v1, this request is independent of new OCPP event IDs.
Only genuine MeterValues SoC samples are exposed; no RFID or raw payloads.
"""
from __future__ import annotations

from pathlib import Path

from ocpp_csms.schema import source_id
from ocpp_csms.transactions.export_metadata import _soc_endpoints
from ocpp_csms.transactions.query import TransactionQuery

SCHEMA = "ocpp-csms/transaction-soc/v1"


def transaction_soc(data_dir: str | Path, *, cp: str, transaction: int) -> dict:
    if not isinstance(cp, str) or not cp.strip() or type(transaction) is not int or transaction < 1:
        raise ValueError("Explicit --cp and positive --transaction are required")
    # Source identity is persisted by the authoritative operational database.
    identity = source_id(data_dir)
    view = TransactionQuery(data_dir).get(transaction)
    if view is None or view.charge_point_id != cp:
        raise ValueError("Transaction not found for the selected charge point")
    start, end = _soc_endpoints(view.record)
    return {
        "schema": SCHEMA,
        "source_id": identity,
        "charger_id": cp,
        "transaction_id": transaction,
        "soc_start": start,
        "soc_end": end,
        "provenance": "ocpp-meter-values",
    }
