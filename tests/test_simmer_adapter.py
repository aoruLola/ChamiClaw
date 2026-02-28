import asyncio

import pytest

from chamiclaw.adapters.simmer import SimmerAdapter
from chamiclaw.core.models import Action, OrderIntent, OrderMode, OrderType, Side


def make_intent() -> OrderIntent:
    return OrderIntent(
        market_id="m1",
        side=Side.YES,
        action=Action.OPEN,
        order_type=OrderType.LIMIT,
        limit_price=0.5,
        size_usd=15.0,
        mode=OrderMode.A,
        thesis="test",
    )


def test_simmer_adapter_returns_simulated_order_in_dry_run():
    adapter = SimmerAdapter()
    result = asyncio.run(adapter.place_order(make_intent(), dry_run=True))
    assert result.accepted is True
    assert result.dry_run is True
    assert result.order_id is not None
    assert result.status == "simulated"


def test_simmer_adapter_requires_base_url_in_live_mode():
    adapter = SimmerAdapter(base_url="")
    with pytest.raises(ValueError):
        asyncio.run(adapter.place_order(make_intent(), dry_run=False))


def test_simmer_adapter_cancel_order_returns_structured_result():
    adapter = SimmerAdapter(base_url="")
    result = asyncio.run(adapter.cancel_order("o-1"))
    assert result.order_id == "o-1"
    assert result.cancelled is True


def test_simmer_adapter_dry_run_uses_idempotency_key_for_stable_order_id():
    adapter = SimmerAdapter()
    intent = make_intent()
    intent.idempotency_key = "idem-123"

    first = asyncio.run(adapter.place_order(intent, dry_run=True))
    second = asyncio.run(adapter.place_order(intent, dry_run=True))

    assert first.order_id == second.order_id
