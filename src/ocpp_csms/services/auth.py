from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthorizationResult:
    id_tag: str
    status: str


class AuthorizationService:
    async def authorize(self, id_tag: str) -> AuthorizationResult:
        return AuthorizationResult(id_tag=id_tag, status="Accepted")
