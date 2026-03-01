from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx
import websockets

from chamiclaw.core.models import NormalizedMarketTick


class CLOBClient:
    def __init__(self, rest_url: str, ws_url: str, timeout_seconds: float = 10.0):
        self.rest_url = rest_url.rstrip("/")
        self.ws_url = ws_url
        self.timeout_seconds = timeout_seconds
        self.reconnect_count = 0

    async def fetch_top_of_book(self, market_id: str) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(f"{self.rest_url}/book", params={"market": market_id})
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    async def fetch_recent_trades(self, market_id: str, limit: int = 50) -> list[dict]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(f"{self.rest_url}/trades", params={"market": market_id, "limit": limit})
            resp.raise_for_status()
            payload = resp.json() if resp.content else []
        return payload if isinstance(payload, list) else []

    async def stream_orderbook(
        self,
        market_ids: list[str],
        *,
        max_retries: int = 10,
        backoff_base_seconds: float = 1.0,
        backoff_max_seconds: float = 30.0,
        stale_timeout_seconds: float = 90.0,
        retry_backoff: float | None = None,
    ):
        if retry_backoff is not None:
            backoff_base_seconds = max(retry_backoff, 0.0)
            backoff_max_seconds = max(backoff_max_seconds, backoff_base_seconds)
        retries = 0
        assets_ids = [str(item).strip() for item in market_ids if str(item).strip()]
        while True:
            try:
                async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
                    subscribe_payload = {
                        "type": "market",
                        "assets_ids": assets_ids,
                        "custom_feature_enabled": True,
                    }
                    await ws.send(json.dumps(subscribe_payload))
                    retries = 0
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=stale_timeout_seconds)
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="ignore")
                        try:
                            payload = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(payload, dict):
                            yield payload
            except Exception:
                retries += 1
                if retries > max_retries:
                    break
                self.reconnect_count += 1
                delay = min(backoff_max_seconds, backoff_base_seconds * (2 ** (retries - 1)))
                await asyncio.sleep(delay)

    def normalize_ws_event(self, payload: dict) -> NormalizedMarketTick | None:
        message_type = str(payload.get("event_type") or payload.get("type") or "").lower()
        if message_type not in {"book", "trade", "trades", "price_change", "last_trade_price"}:
            return None

        market_id = str(payload.get("asset_id") or payload.get("market_id") or payload.get("market") or "").strip()
        if not market_id:
            return None

        best_bid = self._extract_price(payload, primary_key="best_bid", levels_key="bids", side="bid")
        if best_bid is None:
            best_bid = self._as_float(payload.get("bid"))
        best_ask = self._extract_price(payload, primary_key="best_ask", levels_key="asks", side="ask")
        if best_ask is None:
            best_ask = self._as_float(payload.get("ask"))
        last = self._as_float(payload.get("last"))
        if last is None:
            last = self._as_float(payload.get("price"))
        if last is None and best_bid is not None and best_ask is not None:
            last = (best_bid + best_ask) / 2
        if last is None:
            return None

        if best_bid is None:
            best_bid = last
        if best_ask is None:
            best_ask = last
        if best_ask < best_bid:
            best_ask = best_bid

        ts = self._parse_timestamp(payload.get("ts") or payload.get("timestamp"))
        volume_1m = self._as_float(payload.get("volume_1m"))
        if volume_1m is None:
            volume_1m = self._as_float(payload.get("volume"))
        trades_1m = self._as_float(payload.get("trades_1m"))
        if trades_1m is None:
            trades_1m = self._as_float(payload.get("trades"))
        return NormalizedMarketTick(
            market_id=market_id,
            best_bid=best_bid,
            best_ask=best_ask,
            last=last,
            volume_1m=volume_1m or 0.0,
            trades_1m=int(trades_1m or 0),
            ts=ts,
        )

    @staticmethod
    def _as_float(raw: object) -> float | None:
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _extract_price(
        cls,
        payload: dict,
        *,
        primary_key: str,
        levels_key: str,
        side: str,
    ) -> float | None:
        direct = cls._as_float(payload.get(primary_key))
        if direct is not None:
            return direct

        levels = payload.get(levels_key)
        if not isinstance(levels, list) or len(levels) == 0:
            return None
        candidates: list[float] = []
        for level in levels:
            if isinstance(level, dict):
                candidate = cls._as_float(level.get("price"))
            elif isinstance(level, (list, tuple)) and len(level) > 0:
                candidate = cls._as_float(level[0])
            else:
                candidate = cls._as_float(level)
            if candidate is not None:
                candidates.append(candidate)
        if not candidates:
            return None
        if side == "ask":
            return min(candidates)
        return max(candidates)

    @staticmethod
    def _parse_timestamp(raw: object) -> datetime:
        if isinstance(raw, (int, float)):
            value = float(raw)
            if value > 1e12:
                value = value / 1000.0
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(raw, str) and raw:
            if raw.isdigit():
                value = float(raw)
                if value > 1e12:
                    value = value / 1000.0
                return datetime.fromtimestamp(value, tz=timezone.utc)
            normalized = raw.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normalized)
                if parsed.tzinfo is None:
                    return parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except ValueError:
                pass
        return datetime.now(timezone.utc)
