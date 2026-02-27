from __future__ import annotations

import uuid

from chamiclaw.core.models import OrderIntent


class SimmerAdapter:
    """Default execution adapter stub.

    This class is intentionally minimal and can be swapped with a real Simmer client.
    """

    async def place_order(self, intent: OrderIntent) -> str:
        return f"sim-{uuid.uuid4()}"

    async def cancel_order(self, order_id: str) -> bool:
        return True

    async def fetch_order(self, order_id: str) -> dict:
        return {"order_id": order_id, "status": "filled"}

    async def fetch_positions(self) -> list[dict]:
        return []

    async def fetch_balances(self) -> dict:
        return {"cash": 10_000.0}
