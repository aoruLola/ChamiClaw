from __future__ import annotations

import asyncio
import inspect
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PyClobOrderResult:
    order_id: str
    status: str
    raw: dict[str, Any]


class PyClobAdapterError(RuntimeError):
    pass


def _run_maybe_async(value: Any) -> Any:
    if inspect.isawaitable(value):
        return asyncio.run(value)
    return value


class PyClobAdapter:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.host = str(config.get("apis", {}).get("clob_base", "https://clob.polymarket.com")).rstrip("/")
        self.chain_id = int(config.get("apis", {}).get("chain_id", 137))

        execution = config.get("execution", {})
        self.signature_type = int(execution.get("signature_type", 0))
        self.funder = str(os.getenv("POLYMARKET_FUNDER_ADDRESS", "")).strip()
        self.private_key = str(os.getenv("POLYMARKET_PRIVATE_KEY", "")).strip()

        self.api_key = str(os.getenv("POLYMARKET_API_KEY", "")).strip()
        self.api_secret = str(os.getenv("POLYMARKET_API_SECRET", "")).strip()
        self.api_passphrase = str(os.getenv("POLYMARKET_API_PASSPHRASE", "")).strip()
        cache_path = str(config.get("execution", {}).get("api_creds_cache_path", "data/polymarket_api_creds.json"))
        self.cache_path = Path(cache_path)

        try:
            from py_clob_client.client import ClobClient  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise PyClobAdapterError(f"py-clob-client not available: {exc}") from exc

        self._ClobClient = ClobClient

    def _load_cached_creds(self) -> dict[str, str] | None:
        if not self.cache_path.exists():
            return None
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        api_key = str(data.get("api_key") or "").strip()
        secret = str(data.get("secret") or "").strip()
        passphrase = str(data.get("passphrase") or "").strip()
        if api_key and secret and passphrase:
            return {"api_key": api_key, "secret": secret, "passphrase": passphrase}
        return None

    def _save_cached_creds(self, creds: dict[str, str]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(creds, ensure_ascii=True, indent=2), encoding="utf-8")

    def _ensure_api_creds(self) -> dict[str, str]:
        if self.api_key and self.api_secret and self.api_passphrase:
            return {"api_key": self.api_key, "secret": self.api_secret, "passphrase": self.api_passphrase}

        cached = self._load_cached_creds()
        if cached:
            self.api_key, self.api_secret, self.api_passphrase = cached["api_key"], cached["secret"], cached["passphrase"]
            return cached

        derived = self.create_or_derive_api_creds()
        api_key = str(derived.get("apiKey") or derived.get("api_key") or "").strip()
        secret = str(derived.get("secret") or derived.get("api_secret") or "").strip()
        passphrase = str(derived.get("passphrase") or derived.get("api_passphrase") or "").strip()
        if not (api_key and secret and passphrase):
            raise PyClobAdapterError("unable to derive complete api creds")

        creds = {"api_key": api_key, "secret": secret, "passphrase": passphrase}
        self.api_key, self.api_secret, self.api_passphrase = api_key, secret, passphrase
        self._save_cached_creds(creds)
        return creds

    def _build_client(self, with_creds: bool):
        if not self.private_key:
            raise PyClobAdapterError("POLYMARKET_PRIVATE_KEY missing")

        if with_creds:
            creds = self._ensure_api_creds()
            return self._ClobClient(
                host=self.host,
                chain_id=self.chain_id,
                key=self.private_key,
                creds=creds,
                signature_type=self.signature_type,
                funder=self.funder or None,
            )

        return self._ClobClient(host=self.host, chain_id=self.chain_id, key=self.private_key)

    def create_or_derive_api_creds(self) -> dict[str, Any]:
        client = self._build_client(with_creds=False)
        out: Any = None
        if hasattr(client, "create_or_derive_api_creds"):
            out = _run_maybe_async(client.create_or_derive_api_creds())
        elif hasattr(client, "create_or_derive_api_key"):
            out = _run_maybe_async(client.create_or_derive_api_key())
        else:
            raise PyClobAdapterError("create_or_derive_api_creds not available on py-clob-client")

        if isinstance(out, dict):
            return out
        if hasattr(out, "__dict__") and isinstance(out.__dict__, dict):
            return dict(out.__dict__)
        raise PyClobAdapterError("unsupported api creds response type")

    def place_limit_order(self, token_id: str, side: str, price: float, size: float) -> PyClobOrderResult:
        client = self._build_client(with_creds=True)

        order_args = {
            "token_id": token_id,
            "price": float(price),
            "size": float(size),
            "side": "BUY" if side.startswith("buy") else "SELL",
        }
        order_opts = {"tick_size": "0.001", "neg_risk": False}

        if not hasattr(client, "create_and_post_order"):
            raise PyClobAdapterError("create_and_post_order not available on py-clob-client")

        out = _run_maybe_async(client.create_and_post_order(order_args, order_opts))
        if not isinstance(out, dict):
            raise PyClobAdapterError("create_and_post_order returned non-dict")

        order_id = str(out.get("orderID") or out.get("order_id") or out.get("id") or "")
        status = str(out.get("status") or "submitted").lower()
        if not order_id:
            order_id = f"pyclob-{abs(hash(str(out))) % (10**12)}"

        return PyClobOrderResult(order_id=order_id, status=status, raw=out)
