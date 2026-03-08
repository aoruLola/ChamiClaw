from __future__ import annotations

import json

import httpx

from chamiclaw.core.models import LlmReviewDecision, LlmReviewRequest


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 10.0,
        max_retries: int = 1,
        temperature: float = 0.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(max_retries, 0)
        self.temperature = temperature

    async def review_trade(self, request: LlmReviewRequest) -> LlmReviewDecision:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "You are a trade review engine. Respond with strict JSON containing decision, size_multiplier, confidence, risk_tags, reason_summary.",
                },
                {
                    "role": "user",
                    "content": json.dumps(request.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")),
                },
            ],
        }
        attempts = self.max_retries + 1
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    body = resp.json() if resp.content else {}
                content = self._extract_content(body)
                parsed = json.loads(content)
                return LlmReviewDecision.model_validate(parsed)
            except json.JSONDecodeError as exc:
                raise ValueError("llm response was not valid JSON") from exc
            except Exception as exc:  # pragma: no cover - retry path exercised indirectly
                last_error = exc
        if last_error is None:
            raise ValueError("llm review failed without an explicit error")
        raise last_error

    @staticmethod
    def _extract_content(payload: dict) -> str:
        choices = payload.get("choices", []) if isinstance(payload, dict) else []
        if not choices:
            raise ValueError("llm response missing choices")
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            content = "".join(parts)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("llm response missing message content")
        return content.strip()
