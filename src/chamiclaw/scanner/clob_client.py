from __future__ import annotations

import json
import os
import random
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from chamiclaw.exchange.endpoints import load_clob_endpoints, load_clob_field_map
from chamiclaw.exchange.normalize import parse_orderbook_response


class CLOBClient:
    def __init__(self, base_url: str, config: dict[str, Any] | None = None, path_template: str = "/book") -> None:
        self.base_url = base_url.rstrip("/")
        self.path_template = path_template
        self.orderbook_field_map: dict[str, Any] = {}
        self.last_orderbook_errors: list[dict[str, Any]] = []
        self.last_probe_url: str | None = None
        self.inverted_tiny_threshold = 1e-6
        if config:
            ep = load_clob_endpoints(config).orderbook
            self.path_template = str(config.get("scan", {}).get("orderbook_path_template", ep))
            self.orderbook_field_map = dict(load_clob_field_map(config).get("orderbook", {}) or {})
            self.inverted_tiny_threshold = float(config.get("scan", {}).get("orderbook_inverted_tiny_threshold", 1e-6))

    def _request_json(self, url: str, timeout_sec: float) -> Any:
        headers = {"User-Agent": "ChamiClaw/0.1"}
        api_key = os.getenv("CLOB_API_KEY", "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = Request(url=url, method="GET", headers=headers)
        with urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    def _build_orderbook_url(self, token_id: str) -> tuple[str, dict[str, str]]:
        path = self.path_template
        if "{token_id}" in path:
            path = path.format(token_id=quote(str(token_id), safe=""))
            query_params = {"token_id": str(token_id)}
        elif "{market_id}" in path:
            # force token_id mode even if template uses market_id
            path = "/book?token_id=" + quote(str(token_id), safe="")
            query_params = {"token_id": str(token_id)}
        else:
            if "?" in path:
                path = f"{path}&{urlencode({'token_id': str(token_id)})}"
            else:
                path = f"{path}?{urlencode({'token_id': str(token_id)})}"
            query_params = {"token_id": str(token_id)}
        url = f"{self.base_url}{path}"
        return url, query_params

    def _record_400(self, *, endpoint: str, method: str, query_params: dict[str, Any], market_id: str, token_id: str, exc: HTTPError) -> None:
        response_text = ""
        try:
            if exc.fp is not None:
                response_text = exc.fp.read().decode("utf-8", errors="replace")[:800]
        except Exception:
            response_text = ""
        self.last_orderbook_errors.append(
            {
                "endpoint": endpoint,
                "method": method,
                "query_params": query_params,
                "status_code": int(getattr(exc, "code", 0) or 0),
                "response_text": response_text,
                "market_id": market_id,
                "token_id": token_id,
            }
        )
        self.last_orderbook_errors = self.last_orderbook_errors[-50:]

    def fetch_token_ids(self, condition_id: str) -> tuple[str, str]:
        if not self.base_url:
            return "", ""
        candidates = [
            f"/markets/{condition_id}",
            f"/market/{condition_id}",
            f"/conditions/{condition_id}",
            f"/condition/{condition_id}",
            f"/markets?conditionId={quote(str(condition_id), safe="")}",
            f"/markets?condition_id={quote(str(condition_id), safe="")}",
        ]
        for path in candidates:
            url = f"{self.base_url}{path}"
            try:
                payload = self._request_json(url, timeout_sec=8)
            except Exception:
                continue
            data = payload
            if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                data = payload.get("data")
            if isinstance(data, list) and data:
                payload = data[0]
            if isinstance(payload, dict):
                yes = str(payload.get("yesTokenId") or payload.get("yes_token_id") or "")
                no = str(payload.get("noTokenId") or payload.get("no_token_id") or "")
                if yes and no:
                    return yes, no
                ids = payload.get("clobTokenIds") or payload.get("tokenIds") or payload.get("outcomeTokenIds") or []
                if isinstance(ids, list) and len(ids) >= 2:
                    return str(ids[0]), str(ids[1])
        return "", ""

    def fetch_orderbook_debug(self, token_id: str, market_id: str, timeout_sec: float = 6.0) -> tuple[dict[str, float] | None, list[str], str | None]:
        if not self.base_url:
            return None, [], "base_url_missing"
        url, query_params = self._build_orderbook_url(token_id)
        if self.last_probe_url is None or random.random() < 0.02:
            self.last_probe_url = url
        try:
            payload = self._request_json(url, timeout_sec=timeout_sec)
            keys = list(payload.keys()) if isinstance(payload, dict) else []
            parsed = parse_orderbook_response(payload, field_map=self.orderbook_field_map)
            if parsed is not None:
                yb = float(parsed.get("yes_bid", 0.0) or 0.0)
                ya = float(parsed.get("yes_ask", 0.0) or 0.0)
                if yb > ya:
                    gap = yb - ya
                    if gap <= self.inverted_tiny_threshold:
                        mid = (yb + ya) / 2.0
                        parsed["yes_bid"] = mid
                        parsed["yes_ask"] = mid
                    else:
                        self.last_orderbook_errors.append(
                            {
                                "endpoint": self.path_template,
                                "method": "GET",
                                "query_params": query_params,
                                "status_code": 0,
                                "response_text": "ORDERBOOK_INVERTED",
                                "market_id": market_id,
                                "token_id": token_id,
                            }
                        )
                        self.last_orderbook_errors = self.last_orderbook_errors[-50:]
                        return None, keys, "ORDERBOOK_INVERTED"
            if parsed is None:
                self.last_orderbook_errors.append(
                    {
                        "endpoint": self.path_template,
                        "method": "GET",
                        "query_params": query_params,
                        "status_code": 0,
                        "response_text": "parse_orderbook_none",
                        "market_id": market_id,
                        "token_id": token_id,
                    }
                )
                self.last_orderbook_errors = self.last_orderbook_errors[-50:]
                return None, keys, "parse_orderbook_none"
            return parsed, keys, None
        except HTTPError as exc:
            if int(getattr(exc, "code", 0) or 0) == 400:
                self._record_400(
                    endpoint=self.path_template,
                    method="GET",
                    query_params=query_params,
                    market_id=market_id,
                    token_id=token_id,
                    exc=exc,
                )
            else:
                self.last_orderbook_errors.append(
                    {
                        "endpoint": self.path_template,
                        "method": "GET",
                        "query_params": query_params,
                        "status_code": int(getattr(exc, "code", 0) or 0),
                        "response_text": str(exc),
                        "market_id": market_id,
                        "token_id": token_id,
                    }
                )
                self.last_orderbook_errors = self.last_orderbook_errors[-50:]
            return None, [], f"HTTPError:{exc}"
        except (URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
            self.last_orderbook_errors.append(
                {
                    "endpoint": self.path_template,
                    "method": "GET",
                    "query_params": query_params,
                    "status_code": 0,
                    "response_text": str(exc),
                    "market_id": market_id,
                    "token_id": token_id,
                }
            )
            self.last_orderbook_errors = self.last_orderbook_errors[-50:]
            return None, [], f"{type(exc).__name__}:{exc}"

    def fetch_top_of_book_debug(self, market_id: str, token_id_yes: str, token_id_no: str, timeout_sec: float = 6.0) -> tuple[dict[str, float] | None, dict[str, Any]]:
        diag: dict[str, Any] = {
            "market_id": market_id,
            "token_id_yes": token_id_yes,
            "token_id_no": token_id_no,
            "probe_url": self.last_probe_url,
            "yes_payload_keys": [],
            "no_payload_keys": [],
        }
        if not token_id_yes or not token_id_no:
            return None, {**diag, "error": "DATA_INSUFFICIENT_TOKEN_ID"}

        yes_book, yes_keys, yes_err = self.fetch_orderbook_debug(token_id_yes, market_id=market_id, timeout_sec=timeout_sec)
        no_book, no_keys, no_err = self.fetch_orderbook_debug(token_id_no, market_id=market_id, timeout_sec=timeout_sec)
        diag["yes_payload_keys"] = yes_keys
        diag["no_payload_keys"] = no_keys
        diag["yes_error"] = yes_err
        diag["no_error"] = no_err

        has_yes = yes_book is not None and yes_err is None
        has_no = no_book is not None and no_err is None
        if not has_yes and not has_no:
            return None, {**diag, "error": yes_err or no_err or "orderbook_parse_failed"}

        if has_yes and has_no:
            bbo = {
                "yes_bid": float(yes_book.get("yes_bid")),
                "yes_ask": float(yes_book.get("yes_ask")),
                "no_bid": float(no_book.get("yes_bid")),
                "no_ask": float(no_book.get("yes_ask")),
                "depth_usd": float(yes_book.get("depth_usd", 0.0)) + float(no_book.get("depth_usd", 0.0)),
            }
            return bbo, diag

        if has_yes and not has_no:
            yb = float(yes_book.get("yes_bid"))
            ya = float(yes_book.get("yes_ask"))
            bbo = {
                "yes_bid": yb,
                "yes_ask": ya,
                "no_bid": max(0.0, 1.0 - ya),
                "no_ask": min(1.0, 1.0 - yb),
                "depth_usd": float(yes_book.get("depth_usd", 0.0)),
            }
            return bbo, {**diag, "degraded": "NO_TOKEN_INVALID"}

        nb = float(no_book.get("yes_bid"))
        na = float(no_book.get("yes_ask"))
        bbo = {
            "yes_bid": max(0.0, 1.0 - na),
            "yes_ask": min(1.0, 1.0 - nb),
            "no_bid": nb,
            "no_ask": na,
            "depth_usd": float(no_book.get("depth_usd", 0.0)),
        }
        return bbo, {**diag, "degraded": "YES_TOKEN_INVALID"}
