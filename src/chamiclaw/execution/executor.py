from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from chamiclaw.exchange.endpoints import load_clob_endpoints, load_clob_field_map
from chamiclaw.exchange.normalize import normalize_order_status, parse_order_response
from chamiclaw.exchange.pyclob_adapter import PyClobAdapter, PyClobAdapterError
from chamiclaw.ops.secrets import get_runtime_role
from chamiclaw.utils.time import utc_now_iso


@dataclass
class OrderResult:
    order_id: str
    status: str
    reason: str
    retries: int = 0


class ExecutionEngine:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.execution_cfg = config.get("execution", {})
        self.backend = str(self.execution_cfg.get("backend", "rest")).strip().lower()
        self.base_url = str(config.get("apis", {}).get("clob_base", "")).rstrip("/")
        self.endpoints = load_clob_endpoints(config)
        self.order_field_map = dict(load_clob_field_map(config).get("order", {}) or {})

    def _request_json(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not self.base_url:
            raise RuntimeError("CLOB base URL not configured")
        headers = {"Content-Type": "application/json"}
        api_key = os.getenv("CLOB_API_KEY", "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        data = None if payload is None else json.dumps(payload, ensure_ascii=True).encode("utf-8")
        req = Request(url=f"{self.base_url}{path}", method=method, data=data, headers=headers)
        timeout_sec = float(self.execution_cfg.get("request_timeout_sec", 10))
        with urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
        out = json.loads(raw) if raw else {}
        if not isinstance(out, dict):
            raise RuntimeError("CLOB response is not a JSON object")
        return out

    def _adjust_price(self, side: str, price: float, tick_size: float = 0.001) -> float:
        p = float(price)
        if side in {"buy_yes", "buy_basket", "buy_no"}:
            p += tick_size
        return max(0.0, min(1.0, p))

    def _poll_until_terminal(self, order_id: str, timeout_sec: int) -> str:
        poll_interval_sec = float(self.execution_cfg.get("poll_interval_sec", 3))
        deadline = time.time() + timeout_sec
        status = "submitted"
        while time.time() < deadline:
            time.sleep(max(0.2, poll_interval_sec))
            try:
                status_resp = self._request_json("GET", self.endpoints.order_status.format(order_id=order_id))
                parsed = parse_order_response(status_resp, field_map=self.order_field_map)
                status = parsed["status"] or status
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError):
                continue
            if status in {"filled", "canceled", "rejected"}:
                return status
        return status

    def _place_live_order(self, signal: dict, limit_price: float, quantity: float) -> OrderResult:
        max_retries = int(self.execution_cfg.get("max_retries", 3))
        timeout_sec = int(self.execution_cfg.get("order_timeout_sec", 45))
        min_reprice_interval = float(self.execution_cfg.get("min_reprice_interval_sec", 20))
        tick_size = float(self.execution_cfg.get("price_tick_size", 0.001))
        side = str(signal.get("side", "buy_yes"))
        working_price = float(limit_price)
        total_retries = 0
        last_reprice_ts = 0.0
        final_order_id = str(uuid.uuid4())

        for cycle in range(max_retries + 1):
            signal_id = str(signal.get("signal_id", uuid.uuid4()))
            idem_key = f"{signal_id}:{cycle}"
            payload = {
                "market_id": signal["market_id"],
                "side": side,
                "price": working_price,
                "size": quantity,
                "order_type": "limit",
                "idempotency_key": idem_key,
                "client_order_id": idem_key[:64],
            }
            submit: dict | None = None
            submit_attempts = 0
            for submit_attempts in range(max_retries + 1):
                try:
                    submit = self._request_json("POST", self.endpoints.submit_order, payload)
                    break
                except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError):
                    total_retries += 1
                    if submit_attempts < max_retries:
                        time.sleep(min(2.0, 0.2 * (2**submit_attempts)))
            if submit is None:
                return OrderResult(order_id=final_order_id, status="rejected", reason="PRICE_MOVED", retries=total_retries)

            parsed = parse_order_response(submit, field_map=self.order_field_map)
            order_id = parsed["order_id"] or payload["client_order_id"]
            final_order_id = order_id
            status = parsed["status"]
            if status in {"filled", "canceled", "rejected"}:
                return OrderResult(order_id=order_id, status=status, reason="terminal_on_submit", retries=total_retries)

            status = self._poll_until_terminal(order_id=order_id, timeout_sec=timeout_sec)
            if status in {"filled", "rejected"}:
                return OrderResult(order_id=order_id, status=status, reason="polled_terminal", retries=total_retries)
            try:
                cancel_resp = self._request_json("POST", self.endpoints.cancel_order.format(order_id=order_id), payload={})
                status = normalize_order_status(str(cancel_resp.get("status", "canceled")))
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError):
                status = "canceled"

            if cycle >= max_retries:
                return OrderResult(order_id=order_id, status=status, reason="ORDER_TIMEOUT", retries=total_retries)
            now = time.time()
            wait = max(0.0, min_reprice_interval - (now - last_reprice_ts))
            if wait > 0:
                time.sleep(wait)
            last_reprice_ts = time.time()
            working_price = self._adjust_price(side=side, price=working_price, tick_size=tick_size)

        return OrderResult(order_id=final_order_id, status="canceled", reason="REPRICE_LIMIT", retries=total_retries)

    def _live_precheck_failures(self) -> list[str]:
        failures: list[str] = []
        if get_runtime_role() != "execution":
            failures.append("runtime_role_not_execution")

        if self.backend == "py-clob-client":
            if not os.getenv("POLYMARKET_PRIVATE_KEY", "").strip():
                failures.append("POLYMARKET_PRIVATE_KEY_missing")
            if not self.base_url:
                failures.append("clob_base_missing")
            return failures

        if not self.base_url:
            failures.append("clob_base_missing")
        if not os.getenv("CLOB_API_KEY", "").strip():
            failures.append("CLOB_API_KEY_missing")
        if not self.endpoints.submit_order or not self.endpoints.order_status or not self.endpoints.cancel_order:
            failures.append("clob_endpoints_incomplete")
        return failures

    def place_limit_order(self, signal: dict, limit_price: float, quantity: float) -> OrderResult:
        if self.execution_cfg.get("dry_run", True):
            return OrderResult(order_id=str(uuid.uuid4()), status="submitted", reason="dry_run", retries=0)

        failures = self._live_precheck_failures()
        if failures:
            return OrderResult(
                order_id=str(uuid.uuid4()),
                status="rejected",
                reason="live_precheck_failed:" + ",".join(failures),
                retries=0,
            )

        if self.backend == "py-clob-client":
            token_id = str(signal.get("token_id") or "").strip()
            if not token_id:
                return OrderResult(order_id=str(uuid.uuid4()), status="rejected", reason="token_id_missing_for_pyclob", retries=0)
            try:
                adapter = PyClobAdapter(self.config)
                r = adapter.place_limit_order(token_id=token_id, side=str(signal.get("side", "buy_yes")), price=limit_price, size=quantity)
                return OrderResult(order_id=r.order_id, status=r.status, reason="pyclob", retries=0)
            except PyClobAdapterError as exc:
                return OrderResult(order_id=str(uuid.uuid4()), status="rejected", reason=f"pyclob_error:{exc}", retries=0)
            except Exception as exc:  # pragma: no cover
                return OrderResult(order_id=str(uuid.uuid4()), status="rejected", reason=f"pyclob_unexpected:{exc}", retries=0)

        return self._place_live_order(signal=signal, limit_price=limit_price, quantity=quantity)


    def cancel_order(self, order_id: str) -> OrderResult:
        if self.execution_cfg.get("dry_run", True):
            return OrderResult(order_id=order_id, status="canceled", reason="dry_run_cancel", retries=0)
        failures = self._live_precheck_failures()
        if failures:
            return OrderResult(order_id=order_id, status="rejected", reason="live_precheck_failed:" + ",".join(failures), retries=0)
        try:
            cancel_resp = self._request_json("POST", self.endpoints.cancel_order.format(order_id=order_id), payload={})
            status = normalize_order_status(str(cancel_resp.get("status", "canceled")))
            return OrderResult(order_id=order_id, status=status, reason="cancel_request", retries=0)
        except Exception as exc:
            return OrderResult(order_id=order_id, status="rejected", reason=f"cancel_failed:{exc}", retries=0)

    def execute_order(self, signal: dict, quote: dict) -> tuple[OrderResult, bool]:
        limit_price, quantity = self.build_order(signal=signal, quote=quote)
        res = self.place_limit_order(signal=signal, limit_price=limit_price, quantity=quantity)
        return res, True

    def build_order_intent(self, signal: dict, quote: dict, market: dict, risk_snapshot: dict | None = None) -> dict:
        snapshot = risk_snapshot or {}
        return {
            "market_id": signal["market_id"],
            "spread_bps": quote.get("spread_bps", 0),
            "expected_edge_after_costs_bps": signal.get("expected_edge_after_costs_bps", 0),
            "position_pct": snapshot.get("position_pct", 0.0),
            "cluster_exposure_pct": snapshot.get("cluster_exposure_pct", 0.0),
            "daily_drawdown_pct": snapshot.get("daily_drawdown_pct", 0.0),
            "open_orders_same_market": snapshot.get("open_orders_same_market", 0),
            "end_time_utc": market.get("end_time_utc"),
            "is_add_position": True,
        }

    def build_order(self, signal: dict, quote: dict) -> tuple[float, float]:
        side = signal["side"]
        if side in ("buy_yes", "buy_basket"):
            price = float(quote["yes_ask"])
        else:
            price = float(quote["no_ask"])

        confidence = float(signal.get("confidence", 0.5))
        min_conf = 0.62
        scale = max(0.0, min(1.0, (confidence - min_conf) / (1 - min_conf)))
        quantity = round(10 + 90 * scale, 2)
        return price, quantity

    def order_record(
        self,
        order_id: str,
        signal: dict,
        limit_price: float,
        quantity: float,
        status: str,
        retries: int = 0,
    ) -> dict:
        ts = utc_now_iso()
        return {
            "order_id": order_id,
            "signal_id": signal["signal_id"],
            "market_id": signal["market_id"],
            "side": signal["side"],
            "limit_price": limit_price,
            "quantity": quantity,
            "status": status,
            "retries": retries,
            "created_at_utc": ts,
            "updated_at_utc": ts,
        }
