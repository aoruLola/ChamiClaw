from __future__ import annotations

from typing import Protocol

from chamiclaw.core.models import OrderIntent


class ExecutionAdapter(Protocol):
    async def place_order(self, intent: OrderIntent) -> str: ...

    async def cancel_order(self, order_id: str) -> bool: ...

    async def fetch_order(self, order_id: str) -> dict: ...

    async def fetch_positions(self) -> list[dict]: ...

    async def fetch_balances(self) -> dict: ...
