from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class GammaClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def list_active_markets(self, limit: int = 200, retries: int = 3, timeout: int = 20) -> list[dict[str, Any]]:
        params = {
            "active": "true",
            "closed": "false",
            "limit": str(limit),
            "order": "volume24hr",
            "ascending": "false",
        }
        query = urlencode(params)
        req = Request(f"{self.base_url}/markets?{query}", headers={"User-Agent": "ChamiClaw/0.1"})

        for attempt in range(1, retries + 1):
            try:
                with urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return data if isinstance(data, list) else []
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
                if attempt >= retries:
                    break
                time.sleep(min(1.5 * attempt, 5.0))

        return []

    @staticmethod
    def parse_outcomes(raw_market: dict[str, Any]) -> tuple[list[str], list[float]]:
        outcomes_raw = raw_market.get("outcomes", [])
        prices_raw = raw_market.get("outcomePrices", [])

        if isinstance(outcomes_raw, str):
            if outcomes_raw.startswith("["):
                try:
                    outcomes_raw = json.loads(outcomes_raw)
                except json.JSONDecodeError:
                    outcomes_raw = []
            else:
                outcomes_raw = []
        if isinstance(prices_raw, str):
            if prices_raw.startswith("["):
                try:
                    prices_raw = json.loads(prices_raw)
                except json.JSONDecodeError:
                    prices_raw = []
            else:
                prices_raw = []

        outcomes = [str(x) for x in outcomes_raw]
        prices = []
        for p in prices_raw:
            try:
                prices.append(float(p))
            except (TypeError, ValueError):
                prices.append(0.0)
        return outcomes, prices
