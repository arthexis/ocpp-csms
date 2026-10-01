from __future__ import annotations

import pytest

from ocpp_csms.routing import HandlerRegistry


async def test_unknown_action_raises_clear_error() -> None:
    registry = HandlerRegistry()

    with pytest.raises(NotImplementedError, match="Unsupported OCPP action"):
        await registry.handle("DataTransfer", "CP-001", {})
