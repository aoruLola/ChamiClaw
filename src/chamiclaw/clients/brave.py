from __future__ import annotations

import httpx


class BraveClient:
    def __init__(self, api_key: str, timeout_seconds: float = 10.0):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, count: int = 5) -> list[dict]:
        if not self.api_key:
            return []
        headers = {"X-Subscription-Token": self.api_key}
        params = {"q": query, "count": count}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get("https://api.search.brave.com/res/v1/web/search", params=params, headers=headers)
            resp.raise_for_status()
            payload = resp.json() if resp.content else {}
        results = payload.get("web", {}).get("results", [])
        return results if isinstance(results, list) else []
