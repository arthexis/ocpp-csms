from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol


class Handler(Protocol):
    def handle(self, charge_point_id: str, payload: dict[str, Any]) -> Awaitable[dict[str, Any]]:
        ...


HandlerFunction = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, HandlerFunction] = {}

    def register(self, action: str, handler: Handler) -> None:
        self._handlers[action] = handler.handle

    async def handle(
        self,
        action: str,
        charge_point_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            handler = self._handlers[action]
        except KeyError as exc:
            raise NotImplementedError(f"Unsupported OCPP action: {action}") from exc
        return await handler(charge_point_id, payload)
