from __future__ import annotations

from ocpp_csms.energy_contract import SCHEMA as ENERGY_SCHEMA
from ocpp_csms.evidence.contracts import RAW_SCHEMA as EVENTS_RAW_SCHEMA
from ocpp_csms.evidence.contracts import SCHEMA as EVENTS_SCHEMA
from ocpp_csms.status_contract import STATUS_SCHEMA
from ocpp_csms.transactions.contracts import TRANSACTIONS_SCHEMA

SUPPORTED_CONTRACTS = {"status": STATUS_SCHEMA, "transactions": TRANSACTIONS_SCHEMA, "energy": ENERGY_SCHEMA, "events": EVENTS_SCHEMA}
DIAGNOSTIC_CONTRACTS = {"events_raw": EVENTS_RAW_SCHEMA}
ALL_CONTRACTS = {**SUPPORTED_CONTRACTS, **DIAGNOSTIC_CONTRACTS}
