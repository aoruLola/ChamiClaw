from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from chamiclaw.ops.go_no_go_validation import build_go_no_go_payload
from chamiclaw.ops.secrets import get_secret_access_snapshot


@dataclass
class ProbeResult:
    ok: bool
    detail: str
    status: int | None = None


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout_sec: float = 8.0) -> ProbeResult:
    req = Request(
        url=url,
        data=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8")
            status = getattr(resp, "status", None)
        return ProbeResult(ok=(status is not None and 200 <= status < 300), detail=body[:300], status=status)
    except HTTPError as e:
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = str(e)
        return ProbeResult(ok=False, detail=body[:300], status=e.code)
    except (URLError, TimeoutError, OSError) as e:
        return ProbeResult(ok=False, detail=f"{type(e).__name__}:{e}", status=None)


def check_llm_endpoint(config: dict[str, Any]) -> ProbeResult:
    llm = config.get("llm", {})
    mode = str(llm.get("mode", "mock")).strip().lower()
    if mode != "http":
        return ProbeResult(ok=False, detail="llm.mode is not http")

    endpoint = str(llm.get("llm1_endpoint", "")).strip()
    if not endpoint:
        return ProbeResult(ok=False, detail="llm1 endpoint missing")

    api_key_env = str(llm.get("api_key_env", "CHAMICLAW_LLM_API_KEY"))
    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        return ProbeResult(ok=False, detail=f"env {api_key_env} missing")

    model = str(llm.get("model", "")).strip() or os.getenv("CHAMICLAW_LLM_MODEL", "").strip() or "deepseek-ai/DeepSeek-V3.2"
    if "/chat/completions" in endpoint:
        payload = {
            "model": model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": '{"fair_prob":0.5,"confidence":0.7,"rationale":"probe","risk_tags":[]}'},
            ],
        }
    else:
        payload = {
            "task": "llm1_fair_prob",
            "market_prob": 0.5,
            "features": {"depth_imbalance": 0.0, "sigma_5m": 0.01},
        }

    return _post_json(endpoint, payload, headers={"Authorization": f"Bearer {api_key}"})


def build_deploy_readiness_report(config: dict[str, Any], go_no_go_snapshot: dict[str, Any]) -> dict[str, Any]:
    sec = get_secret_access_snapshot()
    gng = build_go_no_go_payload(go_no_go_snapshot, policy=dict(config.get("go_no_go", {}) or {}))

    failures: list[str] = []
    warnings: list[str] = []

    execution = config.get("execution", {})
    llm = config.get("llm", {})
    reconcile = config.get("reconcile", {})
    apis = config.get("apis", {})

    if bool(execution.get("dry_run", True)):
        failures.append("execution.dry_run=true")
    if sec.role != "execution":
        failures.append("runtime_role_not_execution")

    backend = str(execution.get("backend", "rest")).strip().lower()
    if backend == "py-clob-client":
        if not os.getenv("POLYMARKET_PRIVATE_KEY", "").strip():
            failures.append("env.POLYMARKET_PRIVATE_KEY_missing")
        if not (
            os.getenv("POLYMARKET_API_KEY", "").strip()
            and os.getenv("POLYMARKET_API_SECRET", "").strip()
            and os.getenv("POLYMARKET_API_PASSPHRASE", "").strip()
        ):
            warnings.append("env.POLYMARKET_API_KEY/SECRET/PASSPHRASE_missing_will_auto_derive")
    else:
        if not os.getenv("CLOB_API_KEY", "").strip():
            failures.append("env.CLOB_API_KEY_missing")

    if str(llm.get("mode", "mock")).strip().lower() != "http":
        failures.append("llm.mode!=http")
    if not str(llm.get("llm1_endpoint", "")).strip():
        failures.append("llm1_endpoint_missing")
    if not str(llm.get("llm2_endpoint", "")).strip():
        failures.append("llm2_endpoint_missing")

    if not str(apis.get("clob_base", "")).strip():
        failures.append("apis.clob_base_missing")
    if not str(reconcile.get("exchange_positions_endpoint", "")).strip():
        warnings.append("reconcile.exchange_positions_endpoint_missing")

    if gng.get("flow_verdict") != "GO":
        failures.append("flow_verdict_not_go")
    if gng.get("trading_verdict") != "GO":
        failures.append("trading_verdict_not_go")

    llm_probe = check_llm_endpoint(config)
    if not llm_probe.ok:
        failures.append("llm_endpoint_probe_failed")

    deploy_ready = len(failures) == 0
    return {
        "deploy_ready": deploy_ready,
        "failures": failures,
        "warnings": warnings,
        "role": sec.role,
        "go_no_go": {
            "verdict": gng.get("verdict"),
            "flow_verdict": gng.get("flow_verdict"),
            "trading_verdict": gng.get("trading_verdict"),
            "blockers": gng.get("blockers", []),
        },
        "llm_probe": {"ok": llm_probe.ok, "status": llm_probe.status, "detail": llm_probe.detail},
    }
