from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from chamiclaw.obs.logging import get_logger

logger = get_logger("chamiclaw.webhook")


class WebhookNotifier:
    def __init__(
        self,
        *,
        url: str,
        enabled: bool = True,
        timeout_seconds: float = 5.0,
        max_retries: int = 1,
        service_name: str = "chamiclaw",
        environment: str = "local",
    ) -> None:
        self.url = url
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(max_retries, 0)
        self.service_name = service_name
        self.environment = environment
        self.last_success_ts: datetime | None = None
        self.last_failure_ts: datetime | None = None
        self.last_event_type: str | None = None
        self.failures_total = 0

    async def send(self, event_type: str, summary: str, details: dict) -> bool:
        if not self.enabled or not self.url:
            return False
        payload = {
            "event_type": event_type,
            "ts": datetime.now(timezone.utc).isoformat(),
            "service": self.service_name,
            "environment": self.environment,
            "summary": summary,
            "details": details,
        }
        headers = {"Content-Type": "application/json"}
        last_error = ''
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(self.url, json=payload, headers=headers)
                response.raise_for_status()
                self.last_success_ts = datetime.now(timezone.utc)
                self.last_event_type = event_type
                return True
            except Exception as exc:  # pragma: no cover - exact network errors vary
                last_error = str(exc)
                logger.warning(
                    "webhook_send_failed",
                    event_type=event_type,
                    attempt=attempt + 1,
                    error=last_error,
                    payload=json.dumps(payload, ensure_ascii=False),
                )
        self.last_failure_ts = datetime.now(timezone.utc)
        self.last_event_type = event_type
        self.failures_total += 1
        logger.warning("webhook_send_exhausted", event_type=event_type, error=last_error)
        return False
