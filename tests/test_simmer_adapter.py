import asyncio

import httpx
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


def test_simmer_adapter_fetch_balances_uses_sdk_portfolio_endpoint():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "GET" and request.url.path == "/api/sdk/portfolio":
            return httpx.Response(200, json={"balance_usdc": 123.4, "sim_balance": 125.5})
        return httpx.Response(404, json={"detail": "not found"})

    adapter = SimmerAdapter(
        base_url="https://api.simmer.markets",
        api_key="k",
        transport=httpx.MockTransport(handler),
    )
    bal = asyncio.run(adapter.fetch_balances())
    assert bal.cash == 123.4
    assert bal.equity == 125.5
    assert "GET /api/sdk/portfolio" in seen


def test_simmer_adapter_fetch_positions_uses_sdk_positions_endpoint():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "GET" and request.url.path == "/api/sdk/positions":
            return httpx.Response(
                200,
                json={
                    "positions": [
                        {
                            "market_id": "m1",
                            "side": "YES",
                            "shares_yes": 10,
                            "avg_price": 0.51,
                            "u_pnl": 1.2,
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    adapter = SimmerAdapter(
        base_url="https://api.simmer.markets",
        api_key="k",
        transport=httpx.MockTransport(handler),
    )
    positions = asyncio.run(adapter.fetch_positions())
    assert len(positions) == 1
    assert positions[0].market_id == "m1"
    assert positions[0].size == 10.0
    assert "GET /api/sdk/positions" in seen


def test_simmer_adapter_place_order_live_uses_sdk_trade_endpoint():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "POST" and request.url.path == "/api/sdk/trade":
            return httpx.Response(200, json={"success": True, "trade_id": "t-1", "order_status": "submitted"})
        return httpx.Response(404, json={"detail": "not found"})

    adapter = SimmerAdapter(
        base_url="https://api.simmer.markets",
        api_key="k",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(adapter.place_order(make_intent(), dry_run=False))
    assert result.accepted is True
    assert result.order_id == "t-1"
    assert result.status == "submitted"
    assert "POST /api/sdk/trade" in seen
