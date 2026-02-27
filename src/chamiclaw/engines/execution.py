from __future__ import annotations

from chamiclaw.adapters.base import ExecutionAdapter
from chamiclaw.core.models import ApprovedOrder


class ExecutionEngine:
    def __init__(self, adapter: ExecutionAdapter):
        self.adapter = adapter

    async def execute(self, approved: ApprovedOrder) -> str | None:
        if not approved.approved or approved.intent is None:
            return None
        return await self.adapter.place_order(approved.intent)
