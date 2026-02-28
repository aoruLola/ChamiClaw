from __future__ import annotations

import uuid

import httpx

from chamiclaw.core.models import (
    BalanceSnapshot,
    CancelResult,
    ExecutionResult,
    OrderIntent,
    OrderStatus,
    PositionSnapshot,
    Side,
)


class SimmerAdapter:
    """Simmer adapter with explicit dry-run safety mode."""

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        *,
        timeout_seconds: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def place_order(self, intent: OrderIntent, *, dry_run: bool) -> ExecutionResult:
        idem_key = intent.idempotency_key.strip() if intent.idempotency_key else ""
        if dry_run:
            order_id = f"sim-{idem_key[:20]}" if idem_key else f"sim-{uuid.uuid4()}"
            return ExecutionResult(
                accepted=True,
                order_id=order_id,
                status="simulated",
                dry_run=True,
                raw={"intent": intent.model_dump(mode="json")},
            )

        if not self.base_url:
            raise ValueError("SIMMER_BASE_URL is required for live execution.")

        payload = intent.model_dump(mode="json")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(
                f"{self.base_url}/orders",
                json=payload,
                headers=self._headers(idem_key or None),
            )
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
        order_id = str(data.get("order_id") or data.get("id") or f"sim-{uuid.uuid4()}")
        status = str(data.get("status") or "submitted")
        return ExecutionResult(accepted=True, order_id=order_id, status=status, dry_run=False, raw=data)

    async def cancel_order(self, order_id: str) -> CancelResult:
        if not self.base_url:
            return CancelResult(order_id=order_id, cancelled=True, status="cancelled-simulated")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(f"{self.base_url}/orders/{order_id}/cancel", headers=self._headers())
            if resp.status_code >= 400:
                return CancelResult(order_id=order_id, cancelled=False, status=f"http-{resp.status_code}")
        payload = resp.json() if resp.content else {}
        cancelled = bool(payload.get("cancelled", True))
        status = str(payload.get("status", "cancelled"))
        return CancelResult(order_id=order_id, cancelled=cancelled, status=status, raw=payload)

    async def fetch_order(self, order_id: str) -> OrderStatus:
        if not self.base_url:
            return OrderStatus(order_id=order_id, status="simulated-filled")

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(f"{self.base_url}/orders/{order_id}", headers=self._headers())
            resp.raise_for_status()
            payload = resp.json() if resp.content else {}
        return OrderStatus(order_id=order_id, status=str(payload.get("status", "unknown")), raw=payload)

    async def fetch_positions(self) -> list[PositionSnapshot]:
        if not self.base_url:
            return []
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(f"{self.base_url}/positions", headers=self._headers())
            resp.raise_for_status()
            payload = resp.json() if resp.content else []
        positions: list[PositionSnapshot] = []
        for item in payload:
            positions.append(
                PositionSnapshot(
                    market_id=str(item.get("market_id", "")),
                    side=Side.YES if str(item.get("side", "YES")).upper() == "YES" else Side.NO,
                    size=float(item.get("size", 0.0)),
                    avg_price=float(item.get("avg_price", 0.0)),
                    u_pnl=float(item.get("u_pnl", 0.0)),
                )
            )
        return positions

    async def fetch_balances(self) -> BalanceSnapshot:
        if not self.base_url:
            return BalanceSnapshot(cash=10_000.0, equity=10_000.0)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(f"{self.base_url}/balances", headers=self._headers())
            resp.raise_for_status()
            payload = resp.json() if resp.content else {}
        return BalanceSnapshot(cash=float(payload.get("cash", 0.0)), equity=float(payload.get("equity", 0.0)))
