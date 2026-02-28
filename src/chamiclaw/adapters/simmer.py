from __future__ import annotations

import uuid
from typing import Iterable

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
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.transport = transport

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

        payload = {
            "market_id": intent.market_id,
            "side": intent.side.value.lower(),
            "amount": float(intent.size_usd),
            "action": intent.action.value.lower(),
            "venue": "polymarket",
            "order_type": "GTC",
            "execute": True,
            "dry_run": False,
            "price": float(intent.limit_price),
            "reasoning": intent.thesis,
            "source": "chamiclaw",
        }
        if intent.action.value == "CLOSE":
            payload["which_side_to_close"] = intent.side.value.lower()

        data = await self._post_first_available(
            paths=["/api/sdk/trade", "/orders"],
            json_payload=payload,
            idempotency_key=idem_key or None,
        )
        accepted = bool(data.get("success", True))
        order_id = str(data.get("trade_id") or data.get("order_id") or data.get("id") or f"sim-{uuid.uuid4()}")
        status = str(data.get("order_status") or data.get("status") or ("submitted" if accepted else "rejected"))
        return ExecutionResult(accepted=accepted, order_id=order_id, status=status, dry_run=False, raw=data)

    async def cancel_order(self, order_id: str) -> CancelResult:
        if not self.base_url:
            return CancelResult(order_id=order_id, cancelled=True, status="cancelled-simulated")
        paths = [
            f"/api/user/polymarket-orders/{order_id}/cancel",
            f"/orders/{order_id}/cancel",
        ]
        async with self._client() as client:
            for path in paths:
                resp = await client.post(f"{self.base_url}{path}", headers=self._headers())
                if resp.status_code in {404, 405}:
                    continue
                payload_raw = self._payload(resp)
                payload = payload_raw if isinstance(payload_raw, dict) else {"items": payload_raw}
                if resp.status_code >= 400:
                    return CancelResult(
                        order_id=order_id,
                        cancelled=False,
                        status=f"http-{resp.status_code}",
                        raw=payload,
                    )
                cancelled = bool(payload.get("cancelled", payload.get("success", True)))
                status = str(payload.get("status") or ("cancelled" if cancelled else "cancel_failed"))
                return CancelResult(order_id=order_id, cancelled=cancelled, status=status, raw=payload)
        return CancelResult(order_id=order_id, cancelled=False, status="cancel_not_supported")

    async def fetch_order(self, order_id: str) -> OrderStatus:
        if not self.base_url:
            return OrderStatus(order_id=order_id, status="simulated-filled")
        async with self._client() as client:
            # Simmer SDK exposes open orders in batch; find target order by id.
            for path in ("/api/sdk/orders/open", "/api/user/polymarket-orders"):
                resp = await client.get(f"{self.base_url}{path}", headers=self._headers())
                if resp.status_code in {404, 405}:
                    continue
                if resp.status_code >= 400:
                    continue
                payload_raw = self._payload(resp)
                payload = payload_raw if isinstance(payload_raw, dict) else {"items": payload_raw}
                orders = payload.get("orders", payload) if isinstance(payload, dict) else payload
                if isinstance(orders, list):
                    for row in orders:
                        if not isinstance(row, dict):
                            continue
                        row_id = str(row.get("order_id") or row.get("id") or "")
                        if row_id != order_id:
                            continue
                        status = str(row.get("status") or row.get("order_status") or "unknown")
                        return OrderStatus(order_id=order_id, status=status, raw=row)

            # Fallback legacy direct order endpoint.
            resp = await client.get(f"{self.base_url}/orders/{order_id}", headers=self._headers())
            if resp.status_code < 400:
                payload_raw = self._payload(resp)
                payload = payload_raw if isinstance(payload_raw, dict) else {"items": payload_raw}
                return OrderStatus(order_id=order_id, status=str(payload.get("status", "unknown")), raw=payload)
        return OrderStatus(order_id=order_id, status="unknown")

    async def fetch_positions(self) -> list[PositionSnapshot]:
        if not self.base_url:
            return []
        payload = await self._get_first_available(paths=["/api/sdk/positions", "/positions"])
        raw_positions = payload.get("positions", payload) if isinstance(payload, dict) else payload
        if not isinstance(raw_positions, list):
            return []
        positions: list[PositionSnapshot] = []
        for item in raw_positions:
            if not isinstance(item, dict):
                continue
            shares_yes = self._to_float(item.get("shares_yes"))
            shares_no = self._to_float(item.get("shares_no"))
            side_value = str(item.get("side", "")).upper()
            if side_value not in {"YES", "NO"}:
                side_value = "YES" if shares_yes >= shares_no else "NO"
            size = self._to_float(item.get("size"))
            if size <= 0:
                size = shares_yes if side_value == "YES" else shares_no
            avg_price = self._to_float(item.get("avg_price"))
            if avg_price <= 0 and size > 0:
                avg_price = self._to_float(item.get("cost_basis")) / size
            positions.append(
                PositionSnapshot(
                    market_id=str(item.get("market_id") or item.get("id") or ""),
                    side=Side.YES if side_value == "YES" else Side.NO,
                    size=size,
                    avg_price=avg_price,
                    u_pnl=self._to_float(item.get("u_pnl") or item.get("unrealized_pnl") or item.get("pnl")),
                )
            )
        return [p for p in positions if p.market_id]

    async def fetch_balances(self) -> BalanceSnapshot:
        if not self.base_url:
            return BalanceSnapshot(cash=10_000.0, equity=10_000.0)

        payload = await self._get_first_available(paths=["/api/sdk/portfolio", "/balances"])
        if not isinstance(payload, dict):
            payload = {}
        cash = self._pick_first_float(payload, keys=("cash", "balance_usdc", "balance", "sim_balance"))
        equity = self._pick_first_float(payload, keys=("equity", "total_equity", "sim_balance", "balance_usdc", "cash"))
        return BalanceSnapshot(cash=cash, equity=equity)

    def _pick_first_float(self, payload: dict, *, keys: Iterable[str]) -> float:
        for key in keys:
            value = self._to_float(payload.get(key))
            if value != 0.0:
                return value
        for key in keys:
            value = payload.get(key)
            if value is not None:
                return self._to_float(value)
        return 0.0

    async def _get_first_available(self, *, paths: list[str]) -> dict | list:
        async with self._client() as client:
            for path in paths:
                resp = await client.get(f"{self.base_url}{path}", headers=self._headers())
                if resp.status_code in {404, 405}:
                    continue
                resp.raise_for_status()
                return self._payload(resp)
        raise RuntimeError("No supported endpoint found")

    async def _post_first_available(
        self,
        *,
        paths: list[str],
        json_payload: dict,
        idempotency_key: str | None = None,
    ) -> dict:
        async with self._client() as client:
            for path in paths:
                resp = await client.post(
                    f"{self.base_url}{path}",
                    json=json_payload,
                    headers=self._headers(idempotency_key),
                )
                if resp.status_code in {404, 405}:
                    continue
                resp.raise_for_status()
                payload = self._payload(resp)
                return payload if isinstance(payload, dict) else {"items": payload}
        raise RuntimeError("No supported endpoint found")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport)

    @staticmethod
    def _payload(resp: httpx.Response) -> dict | list:
        payload = resp.json() if resp.content else {}
        return payload if isinstance(payload, (dict, list)) else {}

    @staticmethod
    def _to_float(value: object) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0
