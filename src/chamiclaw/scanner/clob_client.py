from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from chamiclaw.exchange.endpoints import load_clob_endpoints
from chamiclaw.exchange.normalize import parse_orderbook_response


class CLOBClient:
    def __init__(self, base_url: str, config: dict[str, Any] | None = None, path_template: str = "/book?market={market_id}") -> None:
        self.base_url = base_url.rstrip("/")
        self.path_template = path_template
        if config:
            self.path_template = str(config.get("scan", {}).get("orderbook_path_template", load_clob_endpoints(config).orderbook))

    def _request_json(self, url: str, timeout_sec: float) -> Any:
        headers = {"User-Agent": "ChamiClaw/0.1"}
        api_key = os.getenv("CLOB_API_KEY", "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = Request(url=url, method="GET", headers=headers)
        with urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    def fetch_top_of_book(self, market_id: str, timeout_sec: float = 6.0) -> dict[str, float] | None:
        if not self.base_url:
            return None
        path = self.path_template.format(market_id=quote(str(market_id), safe=""))
        url = f"{self.base_url}{path}"
        try:
            payload = self._request_json(url, timeout_sec=timeout_sec)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
            return None
        return parse_orderbook_response(payload)
