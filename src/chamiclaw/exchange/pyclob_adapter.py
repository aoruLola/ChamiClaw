from __future__ import annotations

import asyncio
import inspect
import os
from dataclasses import dataclass
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

        try:
            from py_clob_client.client import ClobClient  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise PyClobAdapterError(f"py-clob-client not available: {exc}") from exc

        self._ClobClient = ClobClient

    def _build_client(self, with_creds: bool):
        if not self.private_key:
            raise PyClobAdapterError("POLYMARKET_PRIVATE_KEY missing")

        if with_creds:
            if not (self.api_key and self.api_secret and self.api_passphrase):
                raise PyClobAdapterError("POLYMARKET_API_KEY/API_SECRET/API_PASSPHRASE missing")
            creds = {
                "api_key": self.api_key,
                "secret": self.api_secret,
                "passphrase": self.api_passphrase,
            }
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
        if hasattr(client, "create_or_derive_api_key"):
            out = _run_maybe_async(client.create_or_derive_api_key())
            if isinstance(out, dict):
                return out
        raise PyClobAdapterError("create_or_derive_api_key not available on py-clob-client")

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
