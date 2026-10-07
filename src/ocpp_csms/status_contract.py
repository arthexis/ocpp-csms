from __future__ import annotations

from typing import Any

from ocpp_csms.output import json_command_result
from ocpp_csms.status import ChargerStatus, ConnectorStatus

STATUS_SCHEMA = "ocpp-csms/status/v1"


def connector_status_data(connector: ConnectorStatus) -> dict[str, Any]:
    transaction = None
    if connector.transaction_id is not None:
        transaction = {
            "id": connector.transaction_id,
            "rfid": connector.id_tag,
            "started_at": connector.started_at,
        }
    return {
        "id": connector.connector_id,
        "status": connector.status,
        "error_code": connector.error_code,
        "transaction": transaction,
    }


def charger_status_data(charger: ChargerStatus) -> dict[str, Any]:
    return {
        "id": charger.charger_id,
        "connected": charger.connected,
        "connected_at": charger.connected_at,
        "protocol": charger.subprotocol,
        "last_seen": charger.last_seen,
        "status": charger.status,
        "error_code": charger.error_code,
        "connectors": [connector_status_data(item) for item in charger.connectors],
    }


def status_contract(
    status: dict[str, Any],
    *,
    charger_id: str | None = None,
    charging_only: bool = False,
) -> dict[str, Any]:
    chargers: list[ChargerStatus] = status.get("chargers", [])
    active_chargers = set(status.get("active_chargers", []))

    if charger_id is not None:
        chargers = [item for item in chargers if item.charger_id == charger_id]
    if charging_only:
        chargers = [item for item in chargers if item.charger_id in active_chargers]

    data = {
        "server": {
            "state": status.get("server", "unknown"),
            "started_at": status.get("started_at"),
        },
        "storage": {
            "database": status.get("database", "unknown"),
            "transactions": status.get("transactions", "unknown"),
        },
        "rfid_authorization": status.get("rfid_authorization"),
        "chargers": [charger_status_data(item) for item in chargers],
    }
    return json_command_result(data, schema=STATUS_SCHEMA)
