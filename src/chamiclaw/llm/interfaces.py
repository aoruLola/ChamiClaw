from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class LlmOutput:
    fair_prob: float
    confidence: float
    rationale: str
    risk_tags: list[str]


class LlmProviderError(RuntimeError):
    pass


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _post_json(url: str, payload: dict[str, Any], timeout_sec: float, headers: dict[str, str] | None = None) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    req = Request(url=url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urlopen(req, timeout=timeout_sec) as resp:
        body = resp.read().decode("utf-8")
    parsed = json.loads(body) if body else {}
    if not isinstance(parsed, dict):
        raise LlmProviderError("LLM provider returned non-object JSON")
    return parsed


class _HttpLlmMixin:
    def __init__(self, config: dict[str, Any]) -> None:
        llm_cfg = config.get("llm", {})
        self.mode = str(llm_cfg.get("mode", "mock")).strip().lower()
        self.timeout_sec = float(llm_cfg.get("request_timeout_sec", 8))
        self.max_retries = int(llm_cfg.get("max_retries", 2))
        self.api_key_env = str(llm_cfg.get("api_key_env", "CHAMICLAW_LLM_API_KEY"))
        self.default_model = str(llm_cfg.get("model", "")).strip() or os.getenv("CHAMICLAW_LLM_MODEL", "").strip()

    def _auth_headers(self) -> dict[str, str]:
        key = os.getenv(self.api_key_env, "").strip()
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _request_with_retry(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not endpoint:
            raise LlmProviderError("LLM endpoint not configured")
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return _post_json(endpoint, payload, timeout_sec=self.timeout_sec, headers=self._auth_headers())
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, LlmProviderError) as exc:
                last_err = exc
                if attempt < self.max_retries:
                    time.sleep(min(2.0, 0.2 * (2**attempt)))
        raise LlmProviderError(f"LLM request failed after retries: {last_err}")

    def _openai_payload(self, task: str, market_prob: float, fair_prob: float | None, features: dict[str, Any]) -> dict[str, Any]:
        model = self.default_model or "moonshotai/Kimi-K2.5"
        schema_hint = {
            "fair_prob": 0.5,
            "confidence": 0.7,
            "rationale": "short reason",
            "risk_tags": [],
        }
        if task == "llm2_validate":
            schema_hint["validated_fair_prob"] = 0.5
        user_payload = {
            "task": task,
            "market_prob": market_prob,
            "fair_prob": fair_prob,
            "features": features,
            "output_json_schema": schema_hint,
        }
        return {
            "model": model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "Return strictly JSON object with numeric probabilities in [0,1].",
                },
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
            ],
        }

    def _normalize_output(self, out: dict[str, Any], fallback_fair: float | None = None) -> dict[str, Any]:
        # Native JSON endpoint
        if "fair_prob" in out or "validated_fair_prob" in out:
            return out

        # OpenAI-compatible chat response
        choices = out.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError as exc:
                    raise LlmProviderError(f"chat completion content is not JSON: {exc}")
                if isinstance(parsed, dict):
                    return parsed

        raise LlmProviderError("LLM response missing expected fields")


class Llm1Generator(_HttpLlmMixin):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.endpoint = str(config.get("llm", {}).get("llm1_endpoint", "")).strip()

    def infer(self, market_prob: float, features: dict[str, Any]) -> LlmOutput:
        if self.mode == "http":
            payload = {
                "task": "llm1_fair_prob",
                "market_prob": market_prob,
                "features": features,
            }
            if "/chat/completions" in self.endpoint:
                payload = self._openai_payload("llm1_fair_prob", market_prob=market_prob, fair_prob=None, features=features)
            out = self._request_with_retry(self.endpoint, payload)
            out = self._normalize_output(out)
            req_id = str(out.get("request_id") or out.get("id") or "")
            rationale = str(out.get("rationale", "http_llm1"))
            if req_id:
                rationale = f"{rationale} | req_id={req_id}"
            return LlmOutput(
                fair_prob=_clamp01(float(out["fair_prob"])),
                confidence=_clamp01(float(out.get("confidence", 0.5))),
                rationale=rationale,
                risk_tags=list(out.get("risk_tags", [])),
            )

        imbalance = float(features.get("depth_imbalance", 0))
        sigma = float(features.get("sigma_5m", 0))
        fair = min(0.99, max(0.01, market_prob + 0.05 * imbalance - 0.2 * sigma))
        return LlmOutput(fair_prob=fair, confidence=min(0.9, max(0.55, 0.7 - sigma)), rationale="mock_llm1_from_imbalance_and_vol", risk_tags=[])


class Llm2Validator(_HttpLlmMixin):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.endpoint = str(config.get("llm", {}).get("llm2_endpoint", "")).strip()

    def validate(self, market_prob: float, fair_prob: float, features: dict[str, Any]) -> LlmOutput:
        if self.mode == "http":
            payload = {
                "task": "llm2_validate",
                "market_prob": market_prob,
                "fair_prob": fair_prob,
                "features": features,
            }
            if "/chat/completions" in self.endpoint:
                payload = self._openai_payload("llm2_validate", market_prob=market_prob, fair_prob=fair_prob, features=features)
            out = self._request_with_retry(self.endpoint, payload)
            out = self._normalize_output(out)
            req_id = str(out.get("request_id") or out.get("id") or "")
            rationale = str(out.get("rationale", "http_llm2"))
            if req_id:
                rationale = f"{rationale} | req_id={req_id}"
            return LlmOutput(
                fair_prob=_clamp01(float(out.get("validated_fair_prob", out.get("fair_prob", fair_prob)))),
                confidence=_clamp01(float(out.get("confidence", 0.5))),
                rationale=rationale,
                risk_tags=list(out.get("risk_tags", [])),
            )

        spread = float(features.get("spread_bps", 0))
        confidence = 0.75
        tags: list[str] = []
        if spread > 120:
            confidence -= 0.2
            tags.append("wide_spread")
        if abs(fair_prob - market_prob) > 0.25:
            confidence -= 0.1
            tags.append("large_model_gap")
        return LlmOutput(fair_prob=fair_prob, confidence=max(0.1, min(0.95, confidence)), rationale="mock_llm2_consistency_check", risk_tags=tags)
