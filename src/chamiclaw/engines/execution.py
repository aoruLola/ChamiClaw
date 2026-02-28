from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Callable

from chamiclaw.adapters.base import ExecutionAdapter
from chamiclaw.core.models import (
    ApprovedOrder,
    BalanceSnapshot,
    CancelResult,
    ExecutionResult,
    OrderIntent,
    OrderStatus,
    PositionSnapshot,
)


class ExecutionEngine:
    def __init__(
        self,
        adapter: ExecutionAdapter,
        dry_run: bool = True,
        *,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
        breaker_failures: int = 5,
        breaker_cooldown_seconds: int = 60,
        now_fn: Callable[[], datetime] | None = None,
    ):
        self.adapter = adapter
        self.dry_run = dry_run
        self.max_retries = max(max_retries, 0)
        self.retry_backoff_seconds = max(retry_backoff_seconds, 0.0)
        self.breaker_failures = max(breaker_failures, 1)
        self.breaker_cooldown_seconds = max(breaker_cooldown_seconds, 1)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._idempotency_cache: dict[str, ExecutionResult] = {}
        self._compensation_queue: OrderedDict[str, OrderIntent] = OrderedDict()
        self._consecutive_failures = 0
        self._circuit_open_until: datetime | None = None

    def set_dry_run(self, enabled: bool) -> None:
        self.dry_run = enabled

    async def execute(self, approved: ApprovedOrder) -> ExecutionResult | None:
        if not approved.approved or approved.intent is None:
            return None
        key = self._build_idempotency_key(approved.intent)
        approved.intent.idempotency_key = key
        if self._is_circuit_open():
            self._enqueue_compensation(approved.intent, key=key)
            return ExecutionResult(
                accepted=False,
                status="circuit_open",
                dry_run=self.dry_run,
                raw={"open_until": self._circuit_open_until.isoformat() if self._circuit_open_until else None},
            )
        cached = self._idempotency_cache.get(key)
        if cached is not None:
            return cached
        last_error: str | None = None
        for attempt in range(self.max_retries + 1):
            try:
                result = await self.adapter.place_order(approved.intent, dry_run=self.dry_run)
            except Exception as exc:
                last_error = str(exc)
                if attempt < self.max_retries and self.retry_backoff_seconds > 0:
                    await asyncio.sleep(self.retry_backoff_seconds * (attempt + 1))
                continue
            if result.accepted:
                self._idempotency_cache[key] = result
                self._consecutive_failures = 0
                self._circuit_open_until = None
                return result
            last_error = result.status or "rejected"
            if attempt < self.max_retries and self.retry_backoff_seconds > 0:
                await asyncio.sleep(self.retry_backoff_seconds * (attempt + 1))
        self._register_failure()
        self._enqueue_compensation(approved.intent, key=key)
        return ExecutionResult(
            accepted=False,
            status="execution_error",
            dry_run=self.dry_run,
            raw={"error": last_error or "unknown"},
        )

    async def cancel(self, order_id: str) -> CancelResult:
        last_result = CancelResult(order_id=order_id, cancelled=False, status="cancel_failed")
        for attempt in range(self.max_retries + 1):
            try:
                result = await self.adapter.cancel_order(order_id)
            except Exception as exc:
                last_result = CancelResult(
                    order_id=order_id,
                    cancelled=False,
                    status="cancel_error",
                    raw={"error": str(exc)},
                )
            else:
                last_result = result
                if result.cancelled:
                    return result
            if attempt < self.max_retries and self.retry_backoff_seconds > 0:
                await asyncio.sleep(self.retry_backoff_seconds * (attempt + 1))
        return last_result

    async def fetch_order_status(self, order_id: str) -> OrderStatus:
        return await self.adapter.fetch_order(order_id)

    async def sync_account_state(self) -> tuple[BalanceSnapshot, list[PositionSnapshot]]:
        balances = await self.adapter.fetch_balances()
        positions = await self.adapter.fetch_positions()
        return balances, positions

    def health_snapshot(self) -> dict[str, object]:
        now = self._now_fn()
        circuit_open = bool(self._circuit_open_until and now < self._circuit_open_until)
        return {
            "consecutive_failures": self._consecutive_failures,
            "circuit_open": circuit_open,
            "circuit_open_until": self._circuit_open_until.isoformat() if self._circuit_open_until else None,
            "max_retries": self.max_retries,
            "breaker_failures": self.breaker_failures,
            "breaker_cooldown_seconds": self.breaker_cooldown_seconds,
            "pending_compensations": len(self._compensation_queue),
        }

    async def drain_compensations(self, max_items: int = 10) -> int:
        drained = 0
        if max_items <= 0:
            return 0
        for key, intent in list(self._compensation_queue.items())[:max_items]:
            if self._is_circuit_open():
                break
            result = await self.execute(ApprovedOrder(approved=True, reason="compensation", intent=intent))
            if result and result.accepted:
                self._compensation_queue.pop(key, None)
                drained += 1
        return drained

    def export_compensations(self) -> dict[str, OrderIntent]:
        exported: dict[str, OrderIntent] = {}
        for key, intent in self._compensation_queue.items():
            exported[key] = OrderIntent.model_validate(intent.model_dump(mode="json"))
        return exported

    def load_compensations(self, payload: dict[str, OrderIntent | dict]) -> int:
        self._compensation_queue = OrderedDict()
        for key, value in payload.items():
            if isinstance(value, OrderIntent):
                intent = value
            else:
                intent = OrderIntent.model_validate(value)
            intent.idempotency_key = intent.idempotency_key or key
            cloned = OrderIntent.model_validate(intent.model_dump(mode="json"))
            self._compensation_queue[key] = cloned
        return len(self._compensation_queue)

    @staticmethod
    def _build_idempotency_key(intent: OrderIntent) -> str:
        if intent.idempotency_key:
            return intent.idempotency_key
        payload = intent.model_dump(mode="json")
        payload.pop("idempotency_key", None)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"intent-{digest}"

    def _register_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.breaker_failures:
            self._circuit_open_until = self._now_fn() + timedelta(seconds=self.breaker_cooldown_seconds)

    def _is_circuit_open(self) -> bool:
        if self._circuit_open_until is None:
            return False
        return self._now_fn() < self._circuit_open_until

    def _enqueue_compensation(self, intent: OrderIntent, *, key: str) -> None:
        if key in self._compensation_queue:
            return
        cloned = OrderIntent.model_validate(intent.model_dump(mode="json"))
        self._compensation_queue[key] = cloned
