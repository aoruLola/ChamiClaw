from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class AlertResult:
    ok: bool
    status: int | None
    detail: str


def post_discord_webhook(webhook_url: str, content: str, timeout_sec: float = 5.0) -> AlertResult:
    payload: dict[str, Any] = {"content": content}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            status = getattr(resp, "status", None)
            return AlertResult(ok=(status is not None and 200 <= status < 300), status=status, detail="ok")
    except urllib.error.HTTPError as e:
        return AlertResult(ok=False, status=e.code, detail=f"http_error:{e.reason}")
    except Exception as e:  # pragma: no cover
        return AlertResult(ok=False, status=None, detail=f"exception:{type(e).__name__}:{e}")


def format_alert_message(level: str, title: str, detail: str, context: dict[str, Any] | None = None) -> str:
    lvl = str(level or "INFO").upper()
    icon = {"INFO": "ℹ️", "WARN": "⚠️", "CRITICAL": "🚨"}.get(lvl, "ℹ️")
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    ctx = json.dumps(context or {}, ensure_ascii=False)
    return f"{icon} [{lvl}] {title}\n{detail}\ncontext={ctx}\nts={ts}"


def post_discord_alert(
    webhook_url: str,
    level: str,
    title: str,
    detail: str,
    context: dict[str, Any] | None = None,
    timeout_sec: float = 5.0,
) -> AlertResult:
    content = format_alert_message(level=level, title=title, detail=detail, context=context)
    return post_discord_webhook(webhook_url=webhook_url, content=content, timeout_sec=timeout_sec)
