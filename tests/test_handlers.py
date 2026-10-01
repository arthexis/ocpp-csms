from __future__ import annotations

from ocpp_csms.composition import build_registry
from ocpp_csms.services.auth import AuthorizationService
from ocpp_csms.services.chargers import ChargerService
from ocpp_csms.services.transactions import TransactionService


async def test_boot_notification_is_permissive_by_default() -> None:
    registry = build_registry(ChargerService(), AuthorizationService(), TransactionService())

    response = await registry.handle(
        "BootNotification",
        "CP-001",
        {
            "charge_point_vendor": "Arthexis",
            "charge_point_model": "Simulator",
        },
    )

    assert response["status"] == "Accepted"
    assert response["interval"] == 60
    assert response["currentTime"].endswith("Z")


async def test_start_transaction_returns_incrementing_transaction_id() -> None:
    transactions = TransactionService()
    registry = build_registry(ChargerService(), AuthorizationService(), transactions)

    first = await registry.handle(
        "StartTransaction",
        "CP-001",
        {
            "connector_id": 1,
            "id_tag": "ABC123",
            "meter_start": 0,
            "timestamp": "2026-10-01T00:00:00Z",
        },
    )
    second = await registry.handle(
        "StartTransaction",
        "CP-001",
        {
            "connector_id": 1,
            "id_tag": "ABC123",
            "meter_start": 10,
            "timestamp": "2026-10-01T00:10:00Z",
        },
    )

    assert first["transactionId"] == 1
    assert second["transactionId"] == 2
    assert first["idTagInfo"]["status"] == "Accepted"
