from __future__ import annotations

from typing import Any

from ocpp_csms.services.auth import AuthorizationService


class AuthorizeHandler:
    def __init__(self, authorization: AuthorizationService) -> None:
        self.authorization = authorization

    async def handle(self, charge_point_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        id_tag = payload["id_tag"]
        result = await self.authorization.authorize(id_tag)
        return {"idTagInfo": {"status": result.status}}
