from __future__ import annotations

from typing import Protocol

from chamiclaw.core.models import (
    BalanceSnapshot,
    CancelResult,
    ExecutionResult,
    OrderIntent,
    OrderStatus,
    PositionSnapshot,
)


class ExecutionAdapter(Protocol):
    async def place_order(self, intent: OrderIntent, *, dry_run: bool) -> ExecutionResult: ...

    async def cancel_order(self, order_id: str) -> CancelResult: ...

    async def fetch_order(self, order_id: str) -> OrderStatus: ...

    async def fetch_positions(self) -> list[PositionSnapshot]: ...

    async def fetch_balances(self) -> BalanceSnapshot: ...
