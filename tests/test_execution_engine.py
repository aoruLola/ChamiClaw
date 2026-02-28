import asyncio
from datetime import datetime, timedelta, timezone

from chamiclaw.adapters.base import ExecutionAdapter
from chamiclaw.core.models import (
    Action,
    ApprovedOrder,
    BalanceSnapshot,
    CancelResult,
    ExecutionResult,
    OrderIntent,
    OrderMode,
    OrderStatus,
    OrderType,
    PositionSnapshot,
    Side,
)
from chamiclaw.engines.execution import ExecutionEngine


class SpyAdapter(ExecutionAdapter):
    def __init__(self):
        self.last_dry_run: bool | None = None
        self.place_calls = 0

    async def place_order(self, intent: OrderIntent, *, dry_run: bool) -> ExecutionResult:
        self.place_calls += 1
        self.last_dry_run = dry_run
        return ExecutionResult(
            accepted=True,
            order_id="o-1",
            status="simulated" if dry_run else "submitted",
            dry_run=dry_run,
            raw={"market_id": intent.market_id},
        )

    async def cancel_order(self, order_id: str) -> CancelResult:
        return CancelResult(order_id=order_id, cancelled=True, status="cancelled")

    async def fetch_order(self, order_id: str) -> OrderStatus:
        return OrderStatus(order_id=order_id, status="filled")

    async def fetch_positions(self) -> list[PositionSnapshot]:
        return []

    async def fetch_balances(self) -> BalanceSnapshot:
        return BalanceSnapshot(cash=10000.0, equity=10000.0)


class FlakyAdapter(ExecutionAdapter):
    def __init__(self, fail_times: int):
        self.remaining_failures = fail_times
        self.calls = 0

    async def place_order(self, intent: OrderIntent, *, dry_run: bool) -> ExecutionResult:
        self.calls += 1
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise RuntimeError("transient failure")
        return ExecutionResult(
            accepted=True,
            order_id="o-retry",
            status="submitted",
            dry_run=dry_run,
            raw={"market_id": intent.market_id},
        )

    async def cancel_order(self, order_id: str) -> CancelResult:
        return CancelResult(order_id=order_id, cancelled=True, status="cancelled")

    async def fetch_order(self, order_id: str) -> OrderStatus:
        return OrderStatus(order_id=order_id, status="filled")

    async def fetch_positions(self) -> list[PositionSnapshot]:
        return []

    async def fetch_balances(self) -> BalanceSnapshot:
        return BalanceSnapshot(cash=10000.0, equity=10000.0)


class FlakyCancelAdapter(ExecutionAdapter):
    def __init__(self, fail_times: int):
        self.remaining_failures = fail_times
        self.cancel_calls = 0

    async def place_order(self, intent: OrderIntent, *, dry_run: bool) -> ExecutionResult:
        return ExecutionResult(accepted=True, order_id="o-cancel", status="submitted", dry_run=dry_run, raw={})

    async def cancel_order(self, order_id: str) -> CancelResult:
        self.cancel_calls += 1
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            return CancelResult(order_id=order_id, cancelled=False, status="retry")
        return CancelResult(order_id=order_id, cancelled=True, status="cancelled")

    async def fetch_order(self, order_id: str) -> OrderStatus:
        return OrderStatus(order_id=order_id, status="filled")

    async def fetch_positions(self) -> list[PositionSnapshot]:
        return []

    async def fetch_balances(self) -> BalanceSnapshot:
        return BalanceSnapshot(cash=10000.0, equity=10000.0)


def make_approved_intent() -> ApprovedOrder:
    intent = OrderIntent(
        market_id="m1",
        side=Side.YES,
        action=Action.OPEN,
        order_type=OrderType.LIMIT,
        limit_price=0.5,
        size_usd=20.0,
        mode=OrderMode.A,
        thesis="unit-test",
    )
    return ApprovedOrder(approved=True, reason="ok", intent=intent)


def test_execution_engine_passes_dry_run_to_adapter():
    adapter = SpyAdapter()
    engine = ExecutionEngine(adapter=adapter, dry_run=True)

    result = asyncio.run(engine.execute(make_approved_intent()))

    assert result is not None
    assert result.dry_run is True
    assert adapter.last_dry_run is True


def test_execution_engine_returns_none_for_rejected_order():
    adapter = SpyAdapter()
    engine = ExecutionEngine(adapter=adapter, dry_run=True)

    result = asyncio.run(engine.execute(ApprovedOrder(approved=False, reason="blocked", intent=None)))

    assert result is None


def test_execution_engine_sync_account_state():
    adapter = SpyAdapter()
    engine = ExecutionEngine(adapter=adapter, dry_run=True)

    balances, positions = asyncio.run(engine.sync_account_state())

    assert balances.cash == 10000.0
    assert positions == []


def test_execution_engine_execute_is_idempotent_for_same_intent():
    adapter = SpyAdapter()
    engine = ExecutionEngine(adapter=adapter, dry_run=True)
    approved = make_approved_intent()

    first = asyncio.run(engine.execute(approved))
    second = asyncio.run(engine.execute(approved))

    assert first is not None
    assert second is not None
    assert first.order_id == second.order_id
    assert adapter.place_calls == 1


def test_execution_engine_retries_transient_failures_then_succeeds():
    adapter = FlakyAdapter(fail_times=2)
    engine = ExecutionEngine(adapter=adapter, dry_run=True, max_retries=2, retry_backoff_seconds=0.0)

    result = asyncio.run(engine.execute(make_approved_intent()))

    assert result is not None
    assert result.accepted is True
    assert adapter.calls == 3


def test_execution_engine_opens_circuit_after_failure_threshold():
    adapter = FlakyAdapter(fail_times=10)
    clock = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}

    def now_fn() -> datetime:
        return clock["now"]

    engine = ExecutionEngine(
        adapter=adapter,
        dry_run=True,
        max_retries=0,
        retry_backoff_seconds=0.0,
        breaker_failures=2,
        breaker_cooldown_seconds=60,
        now_fn=now_fn,
    )
    approved = make_approved_intent()

    first = asyncio.run(engine.execute(approved))
    second = asyncio.run(engine.execute(approved))
    third = asyncio.run(engine.execute(approved))

    assert first is not None and first.accepted is False
    assert second is not None and second.accepted is False
    assert third is not None and third.status == "circuit_open"
    assert adapter.calls == 2

    clock["now"] = clock["now"] + timedelta(seconds=61)
    fourth = asyncio.run(engine.execute(approved))
    assert fourth is not None
    assert adapter.calls == 3


def test_execution_engine_failed_execute_enqueues_compensation():
    adapter = FlakyAdapter(fail_times=10)
    engine = ExecutionEngine(adapter=adapter, dry_run=True, max_retries=0)

    result = asyncio.run(engine.execute(make_approved_intent()))

    assert result is not None
    assert result.accepted is False
    health = engine.health_snapshot()
    assert health["pending_compensations"] == 1


def test_execution_engine_can_drain_compensation_queue():
    adapter = FlakyAdapter(fail_times=1)
    engine = ExecutionEngine(adapter=adapter, dry_run=True, max_retries=0)
    _ = asyncio.run(engine.execute(make_approved_intent()))
    before = engine.health_snapshot()["pending_compensations"]

    drained = asyncio.run(engine.drain_compensations(max_items=5))
    after = engine.health_snapshot()["pending_compensations"]

    assert before == 1
    assert drained == 1
    assert after == 0


def test_execution_engine_cancel_retries_until_success():
    adapter = FlakyCancelAdapter(fail_times=2)
    engine = ExecutionEngine(adapter=adapter, dry_run=True, max_retries=2, retry_backoff_seconds=0.0)

    result = asyncio.run(engine.cancel("o-can"))

    assert result.cancelled is True
    assert adapter.cancel_calls == 3


def test_execution_engine_can_export_and_restore_compensations():
    adapter = FlakyAdapter(fail_times=1)
    engine = ExecutionEngine(adapter=adapter, dry_run=True, max_retries=0)
    _ = asyncio.run(engine.execute(make_approved_intent()))
    exported = engine.export_compensations()

    engine2 = ExecutionEngine(adapter=FlakyAdapter(fail_times=0), dry_run=True, max_retries=0)
    engine2.load_compensations(exported)

    assert engine2.health_snapshot()["pending_compensations"] == 1
