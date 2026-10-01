from __future__ import annotations

from typing import Any

from ocpp_csms.services.transactions import TransactionService


class MeterValuesHandler:
    def __init__(self, transactions: TransactionService) -> None:
        self.transactions = transactions

    async def handle(self, charge_point_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self.transactions.record_meter_values(charge_point_id, payload)
        return {}
