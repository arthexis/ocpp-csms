from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Transaction:
    transaction_id: int
    charge_point_id: str
    start_payload: dict[str, Any]
    stop_payload: dict[str, Any] | None = None
    meter_values: list[dict[str, Any]] = field(default_factory=list)


class TransactionService:
    def __init__(self) -> None:
        self._next_transaction_id = 1
        self._transactions: dict[int, Transaction] = {}

    async def start(self, charge_point_id: str, payload: dict[str, Any]) -> Transaction:
        transaction = Transaction(
            transaction_id=self._next_transaction_id,
            charge_point_id=charge_point_id,
            start_payload=payload,
        )
        self._next_transaction_id += 1
        self._transactions[transaction.transaction_id] = transaction
        return transaction

    async def stop(self, charge_point_id: str, payload: dict[str, Any]) -> None:
        transaction_id = payload["transaction_id"]
        transaction = self._transactions[transaction_id]
        if transaction.charge_point_id != charge_point_id:
            raise ValueError("Transaction belongs to another charge point")
        transaction.stop_payload = payload

    async def record_meter_values(self, charge_point_id: str, payload: dict[str, Any]) -> None:
        transaction_id = payload.get("transaction_id")
        if transaction_id is None:
            return
        transaction = self._transactions[transaction_id]
        if transaction.charge_point_id != charge_point_id:
            raise ValueError("Transaction belongs to another charge point")
        transaction.meter_values.append(payload)
