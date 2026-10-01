from __future__ import annotations

from typing import Any

from ocpp_csms.services.auth import AuthorizationService
from ocpp_csms.services.transactions import TransactionService


class StartTransactionHandler:
    def __init__(
        self,
        authorization: AuthorizationService,
        transactions: TransactionService,
    ) -> None:
        self.authorization = authorization
        self.transactions = transactions

    async def handle(self, charge_point_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        auth = await self.authorization.authorize(payload["id_tag"])
        transaction = await self.transactions.start(charge_point_id, payload)
        return {
            "transactionId": transaction.transaction_id,
            "idTagInfo": {"status": auth.status},
        }


class StopTransactionHandler:
    def __init__(self, transactions: TransactionService) -> None:
        self.transactions = transactions

    async def handle(self, charge_point_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self.transactions.stop(charge_point_id, payload)
        return {}
