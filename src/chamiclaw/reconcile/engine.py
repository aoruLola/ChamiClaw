from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from chamiclaw.db.sqlite import Database
from chamiclaw.exchange.endpoints import load_clob_endpoints, load_clob_field_map
from chamiclaw.exchange.normalize import parse_positions_response
from chamiclaw.ops.alerting import post_discord_alert
from chamiclaw.ops.state_machine import SystemStateMachine
from chamiclaw.utils.time import utc_now_iso


@dataclass
class ReconcileConfig:
    exchange_positions_endpoint: str
    qty_tolerance: float
    pause_mismatch_threshold: int
    auto_repair_local: bool
    timeout_sec: float = 10.0


class ReconcileEngine:
    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self.positions_field_map = dict(load_clob_field_map(cfg).get("positions", {}) or {})
        rec = cfg.get("reconcile", {})
        endpoint = str(rec.get("exchange_positions_endpoint", "")).strip()
        if not endpoint:
            endpoints = load_clob_endpoints(cfg)
            endpoint = f"{str(cfg.get('apis', {}).get('clob_base', '')).rstrip('/')}{endpoints.positions}"
        self.cfg = ReconcileConfig(
            exchange_positions_endpoint=endpoint,
            qty_tolerance=float(rec.get("qty_tolerance", 0.0001)),
            pause_mismatch_threshold=int(rec.get("pause_mismatch_threshold", 1)),
            auto_repair_local=bool(rec.get("auto_repair_local", True)),
            timeout_sec=float(rec.get("timeout_sec", 10)),
        )

    def fetch_exchange_positions(self) -> dict:
        url = self.cfg.exchange_positions_endpoint
        if not url:
            return {}
        headers = {"User-Agent": "ChamiClaw/0.1"}
        api_key = os.getenv("CLOB_API_KEY", "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = Request(url=url, method="GET", headers=headers)
        try:
            with urlopen(req, timeout=self.cfg.timeout_sec) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else []
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
            return {}
        return parse_positions_response(data, field_map=self.positions_field_map)

    def compare(self, local_positions: dict, exchange_positions: dict) -> dict:
        mismatches: list[dict] = []
        all_markets = set(local_positions.keys()) | set(exchange_positions.keys())

        for market_id in all_markets:
            local = local_positions.get(market_id, {"yes_qty": 0.0, "no_qty": 0.0})
            remote = exchange_positions.get(market_id, {"yes_qty": 0.0, "no_qty": 0.0})
            if abs(float(local.get("yes_qty", 0.0)) - float(remote.get("yes_qty", 0.0))) > self.cfg.qty_tolerance or abs(float(local.get("no_qty", 0.0)) - float(remote.get("no_qty", 0.0))) > self.cfg.qty_tolerance:
                mismatches.append({"market_id": market_id, "local": local, "remote": remote})

        state = "RUNNING" if len(mismatches) < self.cfg.pause_mismatch_threshold else "PAUSED"
        return {
            "ts_utc": utc_now_iso(),
            "state": state,
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
        }

    def run(self, db: Database, apply_state: bool = True) -> dict:
        with db.connect() as conn:
            local_rows = conn.execute("SELECT market_id, yes_qty, no_qty FROM positions").fetchall()
        local = {str(r[0]): {"yes_qty": float(r[1]), "no_qty": float(r[2])} for r in local_rows}
        exchange = self.fetch_exchange_positions()
        result = self.compare(local_positions=local, exchange_positions=exchange)

        repaired = 0
        if self.cfg.auto_repair_local:
            for mm in result["mismatches"]:
                remote = mm["remote"]
                db.upsert_position(mm["market_id"], float(remote.get("yes_qty", 0.0)), float(remote.get("no_qty", 0.0)))
                repaired += 1
        if apply_state:
            sm = SystemStateMachine()
            if result["state"] == "PAUSED":
                sm.transition("PAUSED", f"reconcile_mismatch:{result['mismatch_count']}")
            else:
                sm.transition("RUNNING", "reconcile_ok")
        db.insert_audit_event(
            level="WARN" if result["mismatch_count"] else "INFO",
            category="reconcile",
            code="RECONCILE_RESULT",
            message=f"mismatch_count={result['mismatch_count']}",
            context={"repaired": repaired},
        )
        webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        if webhook and result["mismatch_count"] > 0:
            post_discord_alert(
                webhook_url=webhook,
                level="CRITICAL" if result["state"] == "PAUSED" else "WARN",
                title="Reconcile mismatch",
                detail=f"mismatch_count={result['mismatch_count']}, repaired={repaired}",
                context={"state": result["state"]},
            )
        result["repaired"] = repaired
        return result
