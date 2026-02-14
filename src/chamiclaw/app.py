from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import time
from typing import Any

from chamiclaw.db.sqlite import Database
from chamiclaw.evaluate.paper import evaluate_signal_horizons
from chamiclaw.execution.executor import ExecutionEngine
from chamiclaw.ops.alerting import post_discord_alert
from chamiclaw.ops.signal_order_log import emit_reject_summary, emit_signal_decision
from chamiclaw.ops.state_machine import SystemStateMachine
from chamiclaw.risk.engine import RiskEngine
from chamiclaw.scanner.clob_client import CLOBClient
from chamiclaw.scanner.gamma_client import GammaClient
from chamiclaw.scanner.market_scanner import scan_markets
from chamiclaw.scanner.quote_collector import build_quote
from chamiclaw.signal.engine import SignalEngine
from chamiclaw.signal.structural import calc_pair_cost_edge_bps
from chamiclaw.utils.json_logger import JsonLogger


@dataclass
class PipelineResult:
    scanned_markets: int
    quotes_written: int
    signals_generated: int
    orders_submitted: int
    signal_drop_counts: dict[str, int]
    max_raw_edge_bps: float = 0.0
    min_spread_bps: float = 0.0
    structural_signal_counts: dict[str, int] = field(default_factory=lambda: {"pair_cost_hit": 0, "cross_market_hit": 0, "term_structure_hit": 0})
    bbo_markets: int = 0
    top_edge_market_debug: dict[str, Any] = field(default_factory=dict)
    edge_calc_attempted: int = 0
    edge_calc_success: int = 0
    edge_calc_failed: int = 0
    edge_calc_skipped: int = 0
    edge_calc_errors: list[dict[str, Any]] = field(default_factory=list)
    p50_edge_bps: float = 0.0
    p90_edge_bps: float = 0.0
    p99_edge_bps: float = 0.0
    max_edge_bps: float = 0.0
    top3_edges: list[dict[str, Any]] = field(default_factory=list)
    baseline_mode: bool = True
    active_mm_markets: list[str] = field(default_factory=list)
    active_arbitrage_markets: list[str] = field(default_factory=list)
    inventory_snapshot: dict[str, float] = field(default_factory=dict)
    mm_stage_executed: bool = False
    mm_candidates_count: int = 0
    mm_selected_count: int = 0
    mm_reject_counts: dict[str, int] = field(default_factory=dict)
    mm_sample_rejects: list[dict[str, Any]] = field(default_factory=list)
    total_inventory: float = 0.0
    total_inventory_net: float = 0.0
    total_exposure_ratio: float = 0.0
    daily_pnl: float = 0.0
    risk_status: str = "NORMAL"


class ChamiClawApp:
    def __init__(self, config: dict[str, Any], db: Database, logger: JsonLogger) -> None:
        self.config = config
        self.db = db
        self.logger = logger

        self.gamma = GammaClient(config["apis"]["gamma_base"])
        self.clob = CLOBClient(config["apis"].get("clob_base", ""), config=config)
        self.signal_engine = SignalEngine(config)
        self.risk = RiskEngine(config)
        self.execution = ExecutionEngine(config)
        self.state = SystemStateMachine()

    def _alert(self, level: str, title: str, detail: str, context: dict[str, Any] | None = None) -> None:
        webhook = str(self.config.get("ops", {}).get("discord_webhook", "")).strip()
        if not webhook:
            return
        post_discord_alert(webhook_url=webhook, level=level, title=title, detail=detail, context=context or {})

    def _is_close_only_action(self, signal: dict[str, Any], risk_snapshot: dict[str, Any]) -> bool:
        side = str(signal.get("side", ""))
        yes_qty = float(risk_snapshot.get("yes_qty", 0.0) or 0.0)
        no_qty = float(risk_snapshot.get("no_qty", 0.0) or 0.0)

        # With current buy-only executor, we approximate close-only as reducing net directional imbalance
        # by buying the opposite side against an existing position.
        if side == "buy_no" and yes_qty > no_qty:
            return True
        if side == "buy_yes" and no_qty > yes_qty:
            return True
        return False

    def _bump_run_stats(self, key: str) -> None:
        p = Path("reports/run_stats.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        stats = {"run_started": 0, "run_completed": 0, "run_aborted": 0}
        if p.exists():
            try:
                stats.update(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
        stats[key] = int(stats.get(key, 0)) + 1
        p.write_text(json.dumps(stats, ensure_ascii=True), encoding="utf-8")

    def _portfolio_risk_snapshot(self, quote_by_market_id: dict[str, dict[str, Any]] | None = None) -> dict[str, float]:
        qmap = quote_by_market_id or {}
        equity = max(1.0, float(self.config.get("risk", {}).get("account_equity_usd", 10_000)))
        day_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
        total_inventory = 0.0
        total_inventory_net = 0.0
        total_exposure_usd = 0.0
        with self.db.connect() as conn:
            rows = conn.execute("SELECT market_id, yes_qty, no_qty FROM positions").fetchall()
            pnl_row = conn.execute(
                """
                SELECT COALESCE(SUM(pnl_usd), 0) AS pnl
                FROM trades
                WHERE ts_utc >= ?
                """,
                (day_start,),
            ).fetchone()
        daily_pnl = float((pnl_row["pnl"] if pnl_row else 0.0) or 0.0)
        for row in rows:
            market_id = str(row["market_id"])
            yes_qty = float(row["yes_qty"] or 0.0)
            no_qty = float(row["no_qty"] or 0.0)
            q = qmap.get(market_id)
            if q is None:
                q = self.db.get_latest_quote(market_id) or {}
            yes_mid = float(q.get("yes_mid", 0.5) or 0.5)
            no_mid = float(q.get("no_mid", 0.5) or 0.5)
            yes_notional = yes_qty * yes_mid
            no_notional = no_qty * no_mid
            signed_notional = yes_notional - no_notional
            total_inventory += abs(signed_notional)
            total_inventory_net += signed_notional
            total_exposure_usd += yes_notional + no_notional
        return {
            "equity": float(equity),
            "daily_pnl": float(daily_pnl),
            "daily_drawdown_pct": float(max(0.0, -daily_pnl / equity)),
            "total_inventory": float(total_inventory),
            "total_inventory_net": float(total_inventory_net),
            "total_exposure_ratio": float(total_exposure_usd / equity),
        }

    def _should_auto_recover_daily_halt(self, state: Any) -> bool:
        if str(getattr(state, "state", "")) != "HALTED":
            return False
        reason = str(getattr(state, "reason", ""))
        if "RISK_HALT_DAILY_DRAWDOWN" not in reason:
            return False
        updated_at = str(getattr(state, "updated_at_utc", ""))
        try:
            halted_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return False
        now = datetime.now(timezone.utc)
        return halted_dt.date() < now.date()

    def _load_mm_state(self) -> dict[str, Any]:
        p = Path("reports/mm_state.json")
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {"markets": {}}

    def _save_mm_state(self, state: dict[str, Any]) -> None:
        p = Path("reports/mm_state.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=True), encoding="utf-8")

    def _mm_cancel_all_orders(self, state: dict[str, Any]) -> None:
        markets_state = dict(state.get("markets", {}) or {})
        for market_id in list(markets_state.keys()):
            self._mm_cancel_market_orders(str(market_id), state)

    def _mm_cancel_market_orders(self, market_id: str, state: dict[str, Any]) -> None:
        markets_state = state.setdefault("markets", {})
        ms = markets_state.setdefault(market_id, {"loss_streak": 0, "paused_until": 0.0, "active_orders": {}, "next_requote_at": {}})
        active = dict(ms.get("active_orders", {}) or {})
        next_requote_at = dict(ms.get("next_requote_at", {}) or {})
        for side, oid in list(active.items()):
            if not oid:
                continue
            res = self.execution.cancel_order(str(oid))
            order = {
                "order_id": str(oid),
                "signal_id": None,
                "market_id": market_id,
                "side": side,
                "limit_price": 0.0,
                "quantity": 0.0,
                "status": res.status,
                "retries": res.retries,
                "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            self.db.insert_order(order)
            active[side] = ""
            next_requote_at[side] = 0.0
        ms["active_orders"] = active
        ms["next_requote_at"] = next_requote_at

    def _mm_audit_reject(self, market_id: str, side: str | None, price: float, size: float, reject_reason: str, risk_block_reason: str | None = None) -> None:
        self.db.insert_audit_event(
            level="WARN",
            category="market_making",
            code="MM_REJECT",
            message=reject_reason,
            context={
                "market_id": market_id,
                "side": (side or ""),
                "price": float(price),
                "size": float(size),
                "reject_reason": reject_reason,
                "risk_block_reason": risk_block_reason,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )

    def _run_market_making_mode(
        self,
        markets: list[dict[str, Any]],
        quote_by_market_id: dict[str, dict[str, Any]],
        arbitrage_markets: set[str] | None = None,
    ) -> dict[str, Any]:
        mm_cfg = dict(self.config.get("market_making", {}) or {})
        mm_cfg["enabled"] = True
        mm_cfg["baseline_mode"] = True

        spread_min = float(mm_cfg.get("min_spread_bps", 40))
        min_liq = float(mm_cfg.get("min_market_liquidity_usd", 100_000))
        min_expiry_min = int(mm_cfg.get("min_time_to_expiry_min", 1440))
        quote_offset_ratio = float(mm_cfg.get("quote_offset_ratio", 0.20))
        per_market_expo_pct = float(mm_cfg.get("per_market_exposure_pct", 0.03))
        total_expo_pct = float(mm_cfg.get("total_exposure_pct", 0.20))
        inv_thresh_pct = float(mm_cfg.get("inventory_threshold_pct", 0.02))
        pause_loss_n = int(mm_cfg.get("pause_after_consecutive_losses", 2))
        pause_min = int(mm_cfg.get("pause_minutes", 30))
        mid_move_pause_bps = float(mm_cfg.get("mid_move_pause_bps_5m", 120))
        mid_move_pause_hard_bps = float(mm_cfg.get("mid_move_pause_hard_bps_5m", 180))
        depth_drop_ratio_warn = float(mm_cfg.get("depth_drop_ratio_warn", 0.50))
        depth_drop_ratio_hard = float(mm_cfg.get("depth_drop_ratio_hard", 0.70))
        drawdown_throttle_pct = float(mm_cfg.get("drawdown_throttle_pct", 0.03))
        drawdown_throttle_scale = float(mm_cfg.get("drawdown_throttle_scale", 0.50))
        drawdown_halt_pct = float(mm_cfg.get("drawdown_halt_pct", 0.05))
        requote_min_sec = float(mm_cfg.get("requote_min_sec", 30))
        requote_max_sec = float(mm_cfg.get("requote_max_sec", 60))
        burst_llm_enabled = bool(mm_cfg.get("burst_llm_enabled", True))
        burst_llm_confidence_min = float(mm_cfg.get("burst_llm_confidence_min", 0.65))

        equity = float(self.config.get("risk", {}).get("account_equity_usd", 10_000))
        per_market_cap = equity * per_market_expo_pct
        total_cap = equity * total_expo_pct
        inv_threshold = equity * inv_thresh_pct

        state = self._load_mm_state()
        markets_state = state.setdefault("markets", {})
        now = time.time()
        orders_submitted = 0
        active_mm_markets: set[str] = set()
        inventory_snapshot: dict[str, float] = {}
        mm_candidates_count = 0
        mm_selected_count = 0
        mm_reject_counts: dict[str, int] = {
            "MM_SPREAD_TOO_LOW": 0,
            "MM_LIQUIDITY_TOO_LOW": 0,
            "MM_EXPIRY_TOO_NEAR": 0,
            "MM_INVENTORY_BLOCK": 0,
            "MM_EXPOSURE_BLOCK": 0,
            "MM_COOLDOWN_BLOCK": 0,
            "MM_PAUSED_BLOCK": 0,
            "MM_REQUOTE_WAIT": 0,
            "MM_BURST_PAUSE": 0,
            "MM_ORDER_LIMIT_BLOCK": 0,
            "MM_EXECUTION_REJECTED": 0,
            "MM_OTHER": 0,
        }
        mm_sample_rejects: list[dict[str, Any]] = []
        any_burst_pause = False

        def _rej(reason: str, market_id: str, spread_bps: float, liquidity: float, inventory: float, side: str | None = None) -> None:
            mm_reject_counts[reason] = int(mm_reject_counts.get(reason, 0)) + 1
            if len(mm_sample_rejects) < 5:
                mm_sample_rejects.append({
                    "market_id": market_id,
                    "spread_bps": spread_bps,
                    "liquidity": liquidity,
                    "inventory": inventory,
                    "reason": reason,
                })
            self._mm_audit_reject(market_id, side, 0.0, 0.0, reason, None)

        portfolio = self._portfolio_risk_snapshot(quote_by_market_id=quote_by_market_id)
        daily_pnl = float(portfolio["daily_pnl"])
        daily_drawdown_pct = float(portfolio["daily_drawdown_pct"])
        risk_status = "NORMAL"
        size_scale = 1.0
        if daily_drawdown_pct > drawdown_halt_pct:
            self._mm_cancel_all_orders(state)
            self._save_mm_state(state)
            self.state.transition("HALTED", "RISK_HALT_DAILY_DRAWDOWN")
            self.db.insert_audit_event(
                level="CRITICAL",
                category="market_making",
                code="RISK_HALT_DAILY_DRAWDOWN",
                message="daily drawdown exceeded halt threshold",
                context={
                    "daily_drawdown_pct": daily_drawdown_pct,
                    "threshold": drawdown_halt_pct,
                    "daily_pnl": daily_pnl,
                },
            )
            return {
                "executed": True,
                "orders_submitted": 0,
                "active_mm_markets": [],
                "inventory_snapshot": {},
                "mm_candidates_count": 0,
                "mm_selected_count": 0,
                "mm_reject_counts": mm_reject_counts,
                "mm_sample_rejects": [],
                "total_inventory": float(portfolio["total_inventory"]),
                "total_inventory_net": float(portfolio["total_inventory_net"]),
                "total_exposure_ratio": float(portfolio["total_exposure_ratio"]),
                "daily_pnl": daily_pnl,
                "risk_status": "HALTED",
            }
        if daily_drawdown_pct > drawdown_throttle_pct:
            risk_status = "THROTTLED"
            size_scale = max(0.1, min(1.0, drawdown_throttle_scale))
            self.db.insert_audit_event(
                level="WARN",
                category="market_making",
                code="MM_DRAWDOWN_THROTTLED",
                message="daily drawdown triggered size throttle",
                context={
                    "daily_drawdown_pct": daily_drawdown_pct,
                    "threshold": drawdown_throttle_pct,
                    "scale": size_scale,
                    "daily_pnl": daily_pnl,
                },
            )

        for market in markets:
            market_id = str(market.get("market_id"))
            quote = quote_by_market_id.get(market_id)
            if not quote:
                continue
            mm_candidates_count += 1
            liquidity = float(market.get("liquidity_usd", 0.0) or 0.0)
            spread_bps = float(quote.get("spread_bps", 0.0) or 0.0)
            if not market.get("tradable", False):
                _rej("MM_OTHER", market_id, spread_bps, liquidity, 0.0)
                continue
            if liquidity < min_liq:
                _rej("MM_LIQUIDITY_TOO_LOW", market_id, spread_bps, liquidity, 0.0)
                continue
            if spread_bps < spread_min:
                _rej("MM_SPREAD_TOO_LOW", market_id, spread_bps, liquidity, 0.0)
                continue
            end_time = str(market.get("end_time_utc") or "")
            if end_time:
                try:
                    dt = datetime.fromisoformat(end_time.replace("Z", "+00:00")).astimezone(timezone.utc)
                    mins_left = (dt - datetime.now(timezone.utc)).total_seconds() / 60
                    if mins_left <= min_expiry_min:
                        _rej("MM_EXPIRY_TOO_NEAR", market_id, spread_bps, liquidity, 0.0)
                        continue
                except ValueError:
                    _rej("MM_EXPIRY_TOO_NEAR", market_id, spread_bps, liquidity, 0.0)
                    continue

            ms = markets_state.setdefault(market_id, {"loss_streak": 0, "paused_until": 0.0, "active_orders": {}, "next_requote_at": {}})
            if arbitrage_markets and market_id in arbitrage_markets:
                self._mm_cancel_market_orders(market_id, state)
                ms["paused_until"] = now + float(mm_cfg.get("mm_cooldown_sec", 60))
                _rej("MM_COOLDOWN_BLOCK", market_id, spread_bps, liquidity, 0.0)
                continue
            if float(ms.get("paused_until", 0.0) or 0.0) > now:
                _rej("MM_PAUSED_BLOCK", market_id, spread_bps, liquidity, 0.0)
                continue

            recent = self.db.get_last_quotes(market_id, limit=20)
            if len(recent) >= 2:
                old_mid = float(recent[-1].get("yes_mid", 0.0) or 0.0)
                cur_mid = float(quote.get("yes_mid", 0.0) or 0.0)
                move_bps = abs(cur_mid - old_mid) * 10_000
                cur_depth = float(quote.get("depth_usd", 0.0) or 0.0)
                old_depth = float(recent[-1].get("depth_usd", cur_depth) or cur_depth)
                depth_drop_ratio = (max(0.0, old_depth - cur_depth) / old_depth) if old_depth > 0 else 0.0
                hard_burst = (move_bps > mid_move_pause_hard_bps) or (depth_drop_ratio > depth_drop_ratio_hard)
                warn_burst = (move_bps > mid_move_pause_bps) or (depth_drop_ratio > depth_drop_ratio_warn)
                llm_burst = False
                if (not hard_burst) and warn_burst and burst_llm_enabled:
                    try:
                        out = self.signal_engine.llm1.infer(
                            market_prob=cur_mid,
                            features={
                                "depth_imbalance": quote.get("depth_imbalance", 0.0),
                                "sigma_5m": quote.get("sigma_5m", 0.0),
                                "mid_move_bps_5m": move_bps,
                                "depth_drop_ratio": depth_drop_ratio,
                                "burst_candidate": True,
                                "market_id": market_id,
                            },
                        )
                        conf = float(getattr(out, "confidence", 0.0) or 0.0)
                        if conf >= burst_llm_confidence_min:
                            rationale = str(getattr(out, "rationale", "") or "").lower()
                            tags = [str(x).lower() for x in (getattr(out, "risk_tags", []) or [])]
                            keys = ("burst", "breaking", "spike", "shock", "volatility")
                            llm_burst = any(k in rationale for k in keys) or any(any(k in t for k in keys) for t in tags)
                    except Exception as exc:
                        self.db.insert_audit_event(
                            level="WARN",
                            category="market_making",
                            code="MM_BURST_LLM_ERROR",
                            message="burst llm check failed",
                            context={"error": str(exc), "market_id": market_id},
                        )
                if hard_burst or llm_burst:
                    any_burst_pause = True
                    self._mm_cancel_market_orders(market_id, state)
                    ms["paused_until"] = now + pause_min * 60
                    _rej("MM_BURST_PAUSE", market_id, spread_bps, liquidity, 0.0)
                    self.db.insert_audit_event(
                        level="WARN",
                        category="market_making",
                        code="MM_BURST_PAUSE",
                        message="burst condition paused market making",
                        context={
                            "market_id": market_id,
                            "mid_move_bps_5m": move_bps,
                            "depth_drop_ratio": depth_drop_ratio,
                            "hard_burst": hard_burst,
                            "llm_burst": llm_burst,
                        },
                    )
                    continue

            snap = self.db.build_risk_snapshot(
                market_id=market_id,
                event_id=market.get("event_id"),
                quote=quote,
                account_equity_usd=equity,
            )
            if float(snap.get("position_pct", 0.0) or 0.0) > per_market_expo_pct:
                _rej("MM_EXPOSURE_BLOCK", market_id, spread_bps, liquidity, 0.0)
                continue
            if float(portfolio.get("total_exposure_ratio", 0.0) or 0.0) > total_expo_pct:
                _rej("MM_EXPOSURE_BLOCK", market_id, spread_bps, liquidity, 0.0)
                continue

            yes_bid = float(quote.get("yes_bid", 0.0) or 0.0)
            yes_ask = float(quote.get("yes_ask", 0.0) or 0.0)
            if yes_ask <= yes_bid:
                _rej("MM_OTHER", market_id, spread_bps, liquidity, 0.0)
                continue
            spread = yes_ask - yes_bid
            center = float(quote.get("yes_mid", (yes_bid + yes_ask) / 2.0) or (yes_bid + yes_ask) / 2.0)
            bid_quote = max(0.0, min(1.0, center - spread * quote_offset_ratio))
            ask_quote = max(0.0, min(1.0, center + spread * quote_offset_ratio))

            yes_qty = float(snap.get("yes_qty", 0.0) or 0.0)
            no_qty = float(snap.get("no_qty", 0.0) or 0.0)
            inventory = yes_qty - no_qty
            inventory_snapshot[market_id] = inventory

            allow_buy_yes = True
            allow_buy_no = True
            if inventory > inv_threshold:
                allow_buy_yes = False
            elif inventory < -inv_threshold:
                allow_buy_no = False
            if (not allow_buy_yes) and (not allow_buy_no):
                _rej("MM_INVENTORY_BLOCK", market_id, spread_bps, liquidity, inventory)
                continue
            mm_selected_count += 1

            mm_size = max(10.0, round(min(per_market_cap, total_cap) * 0.001 * size_scale, 2))
            placed_any = False

            if allow_buy_yes:
                signal_buy_yes = {
                    "signal_id": f"mm-{market_id}-{int(now)}-by",
                    "market_id": market_id,
                    "side": "buy_yes",
                    "confidence": 0.55,
                    "expected_edge_after_costs_bps": max(1.0, spread_bps * 0.20),
                    "is_add_position": True,
                    "is_market_making": True,
                }
                token_ids = market.get("clob_token_ids") or []
                if isinstance(token_ids, list) and token_ids:
                    signal_buy_yes["token_id"] = str(token_ids[0])
                active = dict(ms.get("active_orders", {}) or {})
                next_requote_at = dict(ms.get("next_requote_at", {}) or {})
                old_oid = str(active.get("buy_yes") or "")
                next_due = float(next_requote_at.get("buy_yes", 0.0) or 0.0)
                if old_oid and now < next_due:
                    _rej("MM_REQUOTE_WAIT", market_id, spread_bps, liquidity, inventory, side="buy_yes")
                    ms["active_orders"] = active
                    ms["next_requote_at"] = next_requote_at
                    continue
                if old_oid:
                    c = self.execution.cancel_order(old_oid)
                    if c.status not in {"canceled", "filled", "rejected"}:
                        _rej("MM_ORDER_LIMIT_BLOCK", market_id, spread_bps, liquidity, inventory)
                        continue
                    active["buy_yes"] = ""
                intent = self.execution.build_order_intent(signal_buy_yes, quote, market, snap)
                decision = self.risk.check(intent)
                if decision.approved:
                    res = self.execution.place_limit_order(signal_buy_yes, bid_quote, mm_size)
                    order = self.execution.order_record(res.order_id, signal_buy_yes, bid_quote, mm_size, res.status, retries=res.retries)
                    self.db.insert_order(order)
                    orders_submitted += 1
                    placed_any = True
                    if res.status in {"submitted", "new", "partial"}:
                        active["buy_yes"] = res.order_id
                        next_requote_at["buy_yes"] = now + random.uniform(requote_min_sec, max(requote_min_sec, requote_max_sec))
                    else:
                        active["buy_yes"] = ""
                        next_requote_at["buy_yes"] = 0.0
                    idem_key = f"{res.order_id}:{res.status}"
                    pnl_class = "loss" if res.status in {"rejected", "canceled"} else "win"
                    inserted = self.db.insert_mm_pnl_event(idem_key, market_id, "buy_yes", res.status, pnl_class)
                    if inserted:
                        if pnl_class == "win":
                            ms["loss_streak"] = 0
                        else:
                            ms["loss_streak"] = int(ms.get("loss_streak", 0)) + 1
                    if res.status in {"rejected", "canceled"}:
                        _rej("MM_EXECUTION_REJECTED", market_id, spread_bps, liquidity, inventory, side="buy_yes")
                        self._mm_audit_reject(market_id, "buy_yes", bid_quote, mm_size, str(res.reason), None)
                else:
                    _rej("MM_EXPOSURE_BLOCK", market_id, spread_bps, liquidity, inventory, side="buy_yes")
                    self._mm_audit_reject(market_id, "buy_yes", bid_quote, mm_size, "RISK_REJECT", str(decision.reject_code or "RISK_REJECT"))
                ms["active_orders"] = active
                ms["next_requote_at"] = next_requote_at

            if allow_buy_no:
                signal_buy_no = {
                    "signal_id": f"mm-{market_id}-{int(now)}-bn",
                    "market_id": market_id,
                    "side": "buy_no",
                    "confidence": 0.55,
                    "expected_edge_after_costs_bps": max(1.0, spread_bps * 0.20),
                    "is_add_position": True,
                    "is_market_making": True,
                }
                token_ids = market.get("clob_token_ids") or []
                if isinstance(token_ids, list) and len(token_ids) >= 2:
                    signal_buy_no["token_id"] = str(token_ids[1])
                active = dict(ms.get("active_orders", {}) or {})
                next_requote_at = dict(ms.get("next_requote_at", {}) or {})
                old_oid = str(active.get("buy_no") or "")
                next_due = float(next_requote_at.get("buy_no", 0.0) or 0.0)
                if old_oid and now < next_due:
                    _rej("MM_REQUOTE_WAIT", market_id, spread_bps, liquidity, inventory, side="buy_no")
                    ms["active_orders"] = active
                    ms["next_requote_at"] = next_requote_at
                    continue
                if old_oid:
                    c = self.execution.cancel_order(old_oid)
                    if c.status not in {"canceled", "filled", "rejected"}:
                        _rej("MM_ORDER_LIMIT_BLOCK", market_id, spread_bps, liquidity, inventory)
                        continue
                    active["buy_no"] = ""
                intent = self.execution.build_order_intent(signal_buy_no, quote, market, snap)
                decision = self.risk.check(intent)
                if decision.approved:
                    no_quote = max(0.0, min(1.0, 1.0 - ask_quote))
                    res = self.execution.place_limit_order(signal_buy_no, no_quote, mm_size)
                    order = self.execution.order_record(res.order_id, signal_buy_no, no_quote, mm_size, res.status, retries=res.retries)
                    self.db.insert_order(order)
                    orders_submitted += 1
                    placed_any = True
                    if res.status in {"submitted", "new", "partial"}:
                        active["buy_no"] = res.order_id
                        next_requote_at["buy_no"] = now + random.uniform(requote_min_sec, max(requote_min_sec, requote_max_sec))
                    else:
                        active["buy_no"] = ""
                        next_requote_at["buy_no"] = 0.0
                    idem_key = f"{res.order_id}:{res.status}"
                    pnl_class = "loss" if res.status in {"rejected", "canceled"} else "win"
                    inserted = self.db.insert_mm_pnl_event(idem_key, market_id, "buy_no", res.status, pnl_class)
                    if inserted:
                        if pnl_class == "win":
                            ms["loss_streak"] = 0
                        else:
                            ms["loss_streak"] = int(ms.get("loss_streak", 0)) + 1
                    if res.status in {"rejected", "canceled"}:
                        _rej("MM_EXECUTION_REJECTED", market_id, spread_bps, liquidity, inventory, side="buy_no")
                        self._mm_audit_reject(market_id, "buy_no", no_quote, mm_size, str(res.reason), None)
                else:
                    no_quote = max(0.0, min(1.0, 1.0 - ask_quote))
                    _rej("MM_EXPOSURE_BLOCK", market_id, spread_bps, liquidity, inventory, side="buy_no")
                    self._mm_audit_reject(market_id, "buy_no", no_quote, mm_size, "RISK_REJECT", str(decision.reject_code or "RISK_REJECT"))
                ms["active_orders"] = active
                ms["next_requote_at"] = next_requote_at

            if not placed_any:
                continue
            active_mm_markets.add(market_id)
            if int(ms.get("loss_streak", 0)) >= pause_loss_n:
                ms["paused_until"] = now + pause_min * 60

        self._save_mm_state(state)
        latest_portfolio = self._portfolio_risk_snapshot(quote_by_market_id=quote_by_market_id)
        if risk_status == "NORMAL" and any_burst_pause:
            risk_status = "PAUSED_BURST"
        self.db.insert_audit_event(
            level="INFO",
            category="market_making",
            code="RUN_ONCE_MM_RISK_SUMMARY",
            message="stable_mm_summary",
            context={
                "risk_status": risk_status,
                "total_inventory": latest_portfolio["total_inventory"],
                "total_inventory_net": latest_portfolio["total_inventory_net"],
                "total_exposure_ratio": latest_portfolio["total_exposure_ratio"],
                "daily_pnl": latest_portfolio["daily_pnl"],
                "active_mm_markets": sorted(active_mm_markets),
            },
        )
        return {
            "executed": True,
            "orders_submitted": int(orders_submitted),
            "active_mm_markets": sorted(active_mm_markets),
            "inventory_snapshot": inventory_snapshot,
            "mm_candidates_count": int(mm_candidates_count),
            "mm_selected_count": int(mm_selected_count),
            "mm_reject_counts": mm_reject_counts,
            "mm_sample_rejects": mm_sample_rejects[:5],
            "total_inventory": float(latest_portfolio["total_inventory"]),
            "total_inventory_net": float(latest_portfolio["total_inventory_net"]),
            "total_exposure_ratio": float(latest_portfolio["total_exposure_ratio"]),
            "daily_pnl": float(latest_portfolio["daily_pnl"]),
            "risk_status": risk_status,
        }

    def compute_edges_after_bbo(
        self,
        markets_by_id: dict[str, dict[str, Any]],
        quote_by_market_id: dict[str, dict[str, Any]],
        bbo_market_ids: list[str],
    ) -> dict[str, Any]:
        edge_calc_attempted = 0
        edge_calc_success = 0
        edge_calc_failed = 0
        edge_calc_skipped = 0
        edge_calc_errors: list[dict[str, Any]] = []
        edges: list[float] = []
        top3_edges: list[dict[str, Any]] = []
        max_raw_edge_bps = 0.0
        min_spread_bps = 10_000.0
        top_edge_market_debug: dict[str, Any] = {}
        pair_cost_hit_count = 0

        for market_id in bbo_market_ids:
            quote = quote_by_market_id.get(market_id)
            market = markets_by_id.get(market_id, {})
            if quote is None:
                edge_calc_skipped += 1
                continue
            spread_now = float(quote.get("spread_bps", 0.0) or 0.0)
            min_spread_bps = min(min_spread_bps, spread_now)
            edge_calc_attempted += 1
            try:
                if any(quote.get(k) is None for k in ("yes_bid", "yes_ask", "no_bid", "no_ask")):
                    edge_calc_skipped += 1
                    continue
                pair_calc = calc_pair_cost_edge_bps(
                    quote.get("yes_bid"),
                    quote.get("yes_ask"),
                    quote.get("no_bid"),
                    quote.get("no_ask"),
                )
                if not bool(pair_calc.get("pair_sum_valid", False)):
                    edge_calc_skipped += 1
                    continue
                raw_now = float(pair_calc.get("raw_edge_bps", 0.0) or 0.0)
                edge_calc_success += 1
                edges.append(raw_now)
                top3_edges.append({
                    "market_id": market_id,
                    "yes_mid": pair_calc.get("yes_mid"),
                    "no_mid": pair_calc.get("no_mid"),
                    "pair_sum": pair_calc.get("pair_sum"),
                    "raw_edge_bps": pair_calc.get("raw_edge_bps"),
                    "spread_bps": spread_now,
                })
                top3_edges = sorted(top3_edges, key=lambda x: float(x.get("raw_edge_bps") or 0.0), reverse=True)[:3]
                enter_edge = float(self.config.get("signal", {}).get("enter_edge_bps", 180))
                if raw_now >= enter_edge:
                    pair_cost_hit_count += 1
                if raw_now >= max_raw_edge_bps:
                    max_raw_edge_bps = raw_now
                    enter_edge = float(self.config.get("signal", {}).get("enter_edge_bps", 180))
                    pair_hit = float(pair_calc.get("raw_edge_bps", 0.0) or 0.0) >= enter_edge
                    top_edge_market_debug = {
                        "market_id": market_id,
                        "token_ids": {
                            "yes": market.get("token_id_yes") or "",
                            "no": market.get("token_id_no") or "",
                        },
                        "yes_bid_raw": quote.get("yes_bid"),
                        "yes_ask_raw": quote.get("yes_ask"),
                        "no_bid_raw": quote.get("no_bid"),
                        "no_ask_raw": quote.get("no_ask"),
                        "yes_bid": pair_calc.get("yes_bid"),
                        "yes_ask": pair_calc.get("yes_ask"),
                        "no_bid": pair_calc.get("no_bid"),
                        "no_ask": pair_calc.get("no_ask"),
                        "yes_mid": pair_calc.get("yes_mid"),
                        "no_mid": pair_calc.get("no_mid"),
                        "pair_sum": pair_calc.get("pair_sum"),
                        "raw_edge_bps": pair_calc.get("raw_edge_bps"),
                        "enter_edge_bps": enter_edge,
                        "pair_cost_hit": pair_hit,
                        "normalization_scale": pair_calc.get("normalization_scale", "none"),
                    }
            except Exception as exc:
                edge_calc_failed += 1
                edge_calc_errors.append({
                    "market_id": market_id,
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                })
                edge_calc_errors = edge_calc_errors[-3:]

        p50_edge_bps = p90_edge_bps = p99_edge_bps = max_edge_bps = 0.0
        if edges:
            arr = sorted(edges)
            def q(v: float) -> float:
                i = int(round((len(arr)-1)*v))
                i = max(0, min(len(arr)-1, i))
                return float(arr[i])
            p50_edge_bps = q(0.50)
            p90_edge_bps = q(0.90)
            p99_edge_bps = q(0.99)
            max_edge_bps = float(arr[-1])

        return {
            "edge_calc_attempted": edge_calc_attempted,
            "edge_calc_success": edge_calc_success,
            "edge_calc_failed": edge_calc_failed,
            "edge_calc_skipped": edge_calc_skipped,
            "edge_calc_errors": edge_calc_errors,
            "p50_edge_bps": p50_edge_bps,
            "p90_edge_bps": p90_edge_bps,
            "p99_edge_bps": p99_edge_bps,
            "max_edge_bps": max_edge_bps,
            "max_raw_edge_bps": max_raw_edge_bps,
            "min_spread_bps": (0.0 if min_spread_bps == 10_000.0 else min_spread_bps),
            "top3_edges": top3_edges[:3],
            "top_edge_market_debug": top_edge_market_debug,
            "pair_cost_hit_count": pair_cost_hit_count,
        }

    def run_once(self) -> PipelineResult:
        self._bump_run_stats("run_started")
        state = self.state.load()
        if self._should_auto_recover_daily_halt(state):
            self.state.transition("RUNNING", "daily_drawdown_new_day")
            state = self.state.load()
        if state.state == "HALTED":
            self.logger.log("SYSTEM_HALTED", reason=state.reason)
            snap = self._portfolio_risk_snapshot()
            self._bump_run_stats("run_completed")
            return PipelineResult(
                scanned_markets=0,
                quotes_written=0,
                signals_generated=0,
                orders_submitted=0,
                signal_drop_counts={},
                total_inventory=float(snap["total_inventory"]),
                total_inventory_net=float(snap["total_inventory_net"]),
                total_exposure_ratio=float(snap["total_exposure_ratio"]),
                daily_pnl=float(snap["daily_pnl"]),
                risk_status="HALTED",
            )

        cycle_started = time.time()
        timing_ms: dict[str, int] = {}
        llm_degraded_signals = 0

        try:
            markets = scan_markets(self.config, self.gamma)
        except Exception as exc:
            self.state.transition("PAUSED", "scanner_error")
            self.db.insert_audit_event(
                level="ERROR",
                category="scanner",
                code="SCAN_FAIL",
                message=str(exc),
                context={},
            )
            self.logger.log("SCAN_FAIL", error=str(exc))
            self._alert("CRITICAL", "Scanner failure", str(exc), context={"phase": "scan"})
            self._bump_run_stats("run_aborted")
            return PipelineResult(
                scanned_markets=0,
                quotes_written=0,
                signals_generated=0,
                orders_submitted=0,
                signal_drop_counts={},
                max_raw_edge_bps=0.0,
                min_spread_bps=0.0,
                structural_signal_counts={"pair_cost_hit": 0, "cross_market_hit": 0, "term_structure_hit": 0},
                bbo_markets=0,
                top_edge_market_debug={},
                edge_calc_attempted=0,
                edge_calc_success=0,
                edge_calc_failed=0,
                edge_calc_skipped=0,
                edge_calc_errors=[],
                p50_edge_bps=0.0,
                p90_edge_bps=0.0,
                p99_edge_bps=0.0,
                max_edge_bps=0.0,
                top3_edges=[],
            )
        timing_ms["scan"] = int((time.time() - cycle_started) * 1000)

        quotes_written = 0
        signals_generated = 0
        orders_submitted = 0
        signal_drop_counts: dict[str, int] = {}
        max_raw_edge_bps = 0.0
        min_spread_bps = 10_000.0
        structural_signal_counts = {"pair_cost_hit": 0, "cross_market_hit": 0, "term_structure_hit": 0}
        bbo_markets = 0
        bbo_market_ids: list[str] = []
        top_edge_market_debug: dict[str, Any] = {}
        edge_calc_attempted = 0
        edge_calc_success = 0
        edge_calc_failed = 0
        edge_calc_skipped = 0
        edge_calc_errors: list[dict[str, Any]] = []
        top3_edges: list[dict[str, Any]] = []
        p50_edge_bps = 0.0
        p90_edge_bps = 0.0
        p99_edge_bps = 0.0
        max_edge_bps = 0.0
        missing_yes_bid = 0
        missing_yes_ask = 0
        missing_no_bid = 0
        missing_no_ask = 0
        orderbook_calls = 0
        scan_cfg = self.config.get("scan", {})
        orderbook_budget = int(scan_cfg.get("max_orderbook_calls_per_cycle", 40))
        orderbook_for_tradable_only = bool(scan_cfg.get("orderbook_for_tradable_only", True))
        enable_orderbook = bool(scan_cfg.get("enable_orderbook", True))
        reject_counts: dict[str, int] = {}
        top_signal_edges: list[float] = []
        arbitrage_markets: set[str] = set()
        with self.db.connect() as conn:
            total_quotes = int(conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0])
        if total_quotes > 8000:
            enable_orderbook = False
        max_cycle_sec = float(self.config.get("scan", {}).get("max_cycle_runtime_sec", 120))
        quote_by_market_id: dict[str, dict[str, Any]] = {}

        # Phase 1: lightweight scan for ranking only.
        phase1_started = time.time()
        rough_rank: list[tuple[float, str]] = []
        for market in markets:
            if time.time() - cycle_started > max_cycle_sec:
                self.db.insert_audit_event(
                    level="WARN",
                    category="scanner",
                    code="CYCLE_TIME_BUDGET_EXCEEDED",
                    message="run_once time budget exceeded; partial cycle committed",
                    context={"max_cycle_runtime_sec": max_cycle_sec, "processed_quotes": quotes_written},
                )
                self._alert(
                    "WARN",
                    "Cycle runtime budget exceeded",
                    "run_once exited early to avoid watchdog kill",
                    context={"max_cycle_runtime_sec": max_cycle_sec, "processed_quotes": quotes_written},
                )
                self._bump_run_stats("run_aborted")
                return PipelineResult(
                    scanned_markets=len(markets),
                    quotes_written=quotes_written,
                    signals_generated=signals_generated,
                    orders_submitted=orders_submitted,
                    signal_drop_counts=signal_drop_counts,
                    max_raw_edge_bps=max_raw_edge_bps,
                    min_spread_bps=(0.0 if min_spread_bps == 10_000.0 else min_spread_bps),
                    structural_signal_counts=structural_signal_counts,
                    bbo_markets=bbo_markets,
                    top_edge_market_debug=top_edge_market_debug,
                    edge_calc_attempted=edge_calc_attempted,
                    edge_calc_success=edge_calc_success,
                    edge_calc_failed=edge_calc_failed,
                    edge_calc_skipped=edge_calc_skipped,
                    edge_calc_errors=edge_calc_errors,
                    p50_edge_bps=p50_edge_bps,
                    p90_edge_bps=p90_edge_bps,
                    p99_edge_bps=p99_edge_bps,
                    max_edge_bps=max_edge_bps,
                    top3_edges=top3_edges[:3],
                )
            self.db.upsert_market(market)
            prices = market.get("outcome_prices") or [0.5, 0.5]
            try:
                rough_yes = float(prices[0])
            except (TypeError, ValueError, IndexError):
                rough_yes = 0.5
            rough_raw_edge_bps = abs(rough_yes - 0.5) * 10_000
            rough_rank.append((rough_raw_edge_bps, market["market_id"]))

        candidate_market_ids = {mid for _, mid in sorted(rough_rank, reverse=True)[: min(orderbook_budget, len(rough_rank))]}

        # Phase 2: fetch YES/NO orderbook by token_id and write quote only when full BBO is parsed.
        sample_raw_payload_keys: dict[str, dict[str, list[str]]] = {}
        token_id_missing_markets: list[dict[str, Any]] = []
        if enable_orderbook:
            for market in markets:
                if market["market_id"] not in candidate_market_ids:
                    continue
                if orderbook_calls >= orderbook_budget:
                    break
                if orderbook_for_tradable_only and not market.get("tradable", False):
                    continue

                token_ids = [str(x) for x in (market.get("clob_token_ids") or []) if str(x)]
                token_id_yes = str(market.get("token_id_yes") or "")
                token_id_no = str(market.get("token_id_no") or "")
                if (not token_id_yes or not token_id_no) and len(token_ids) >= 2:
                    token_id_yes = token_ids[0]
                    token_id_no = token_ids[1]
                if token_ids and ((token_id_yes not in token_ids) or (token_id_no not in token_ids)):
                    signal_drop_counts["DATA_INSUFFICIENT_TOKEN_MISMATCH"] = signal_drop_counts.get("DATA_INSUFFICIENT_TOKEN_MISMATCH", 0) + 1
                    token_id_missing_markets.append({"market_id": market["market_id"], "missing": {"token_pair_mismatch": True}})
                    continue
                if not token_id_yes or not token_id_no:
                    token_id_missing_markets.append(
                        {
                            "market_id": market["market_id"],
                            "missing": {
                                "token_id_yes": not bool(token_id_yes),
                                "token_id_no": not bool(token_id_no),
                            },
                        }
                    )
                    signal_drop_counts["DATA_INSUFFICIENT_TOKEN_ID"] = signal_drop_counts.get("DATA_INSUFFICIENT_TOKEN_ID", 0) + 1
                    continue

                orderbook, diag = self.clob.fetch_top_of_book_debug(
                    market_id=market["market_id"],
                    token_id_yes=token_id_yes,
                    token_id_no=token_id_no,
                    timeout_sec=float(self.config.get("scan", {}).get("orderbook_timeout_sec", 6)),
                )
                orderbook_calls += 1

                if len(sample_raw_payload_keys) < 2:
                    sample_raw_payload_keys[str(market["market_id"])] = {
                        "yes_payload_keys": diag.get("yes_payload_keys", []),
                        "no_payload_keys": diag.get("no_payload_keys", []),
                    }

                if orderbook is None:
                    missing_yes_bid += 1
                    missing_yes_ask += 1
                    missing_no_bid += 1
                    missing_no_ask += 1
                    continue

                has_yb = orderbook.get("yes_bid") is not None
                has_ya = orderbook.get("yes_ask") is not None
                has_nb = orderbook.get("no_bid") is not None
                has_na = orderbook.get("no_ask") is not None
                if not has_yb:
                    missing_yes_bid += 1
                if not has_ya:
                    missing_yes_ask += 1
                if not has_nb:
                    missing_no_bid += 1
                if not has_na:
                    missing_no_ask += 1
                if not (has_yb and has_ya and has_nb and has_na):
                    continue

                recent = self.db.get_last_quotes(market["market_id"], limit=20)
                recent_yes = [float(x.get("yes_mid", 0)) for x in reversed(recent)]
                quote = build_quote(market, recent_yes, orderbook=orderbook)
                self.db.insert_quote(quote)
                quote_by_market_id[market["market_id"]] = quote
                quotes_written += 1
                bbo_markets += 1
                bbo_market_ids.append(market["market_id"])
        allow_mid_quote_fallback = bool(
            scan_cfg.get("allow_mid_quote_fallback", bool(self.config.get("execution", {}).get("dry_run", False)))
        )
        fallback_spread_bps = float(scan_cfg.get("fallback_spread_bps", 40.0))
        if allow_mid_quote_fallback:
            half_spread = max(0.0, fallback_spread_bps) / 10_000.0 / 2.0
            for market in markets:
                market_id = str(market.get("market_id") or "")
                if not market_id or market_id in quote_by_market_id:
                    continue
                prices = market.get("outcome_prices") or []
                if not isinstance(prices, (list, tuple)) or len(prices) < 2:
                    continue
                try:
                    yes_mid = float(prices[0])
                    no_mid = float(prices[1])
                except (TypeError, ValueError):
                    continue
                orderbook = {
                    "yes_bid": max(0.0, min(1.0, yes_mid - half_spread)),
                    "yes_ask": max(0.0, min(1.0, yes_mid + half_spread)),
                    "no_bid": max(0.0, min(1.0, no_mid - half_spread)),
                    "no_ask": max(0.0, min(1.0, no_mid + half_spread)),
                    "depth_usd": float(market.get("liquidity_usd", 0.0) or 0.0),
                }
                recent = self.db.get_last_quotes(market_id, limit=20)
                recent_yes = [float(x.get("yes_mid", 0.0) or 0.0) for x in reversed(recent)]
                quote = build_quote(market, recent_yes, orderbook=orderbook)
                self.db.insert_quote(quote)
                quote_by_market_id[market_id] = quote
                quotes_written += 1
                bbo_markets += 1
                bbo_market_ids.append(market_id)
        bbo_full_threshold = int(min(30, max(1, int(len(markets) * 0.2))))
        bbo_signal_min = int(scan_cfg.get("min_bbo_for_signal_stage", min(15, max(1, len(markets)))))

        if bbo_markets < bbo_signal_min:
            missing_counts = {
                "missing_yes_bid": missing_yes_bid,
                "missing_yes_ask": missing_yes_ask,
                "missing_no_bid": missing_no_bid,
                "missing_no_ask": missing_no_ask,
            }
            self.db.insert_audit_event(
                level="ERROR",
                category="scanner",
                code="BBO_INSUFFICIENT",
                message="insufficient BBO markets",
                context={
                    "bbo_markets": bbo_markets,
                    "scanned_markets": len(markets),
                    "missing_counts": missing_counts,
                    "last_orderbook_errors": self.clob.last_orderbook_errors[-3:],
                    "sample_raw_payload_keys": sample_raw_payload_keys,
                    "token_id_missing_markets": token_id_missing_markets[:20],
                    "probe_url": self.clob.last_probe_url,
                },
            )
            self.logger.log(
                "BBO_INSUFFICIENT",
                scanned_markets=len(markets),
                bbo_markets=bbo_markets,
                missing_counts=missing_counts,
                last_orderbook_errors=self.clob.last_orderbook_errors[-3:],
                sample_raw_payload_keys=sample_raw_payload_keys,
                token_id_missing_markets=token_id_missing_markets[:20],
                probe_url=self.clob.last_probe_url,
            )
            self._bump_run_stats("run_aborted")
            return PipelineResult(
                scanned_markets=len(markets),
                quotes_written=quotes_written,
                signals_generated=0,
                orders_submitted=0,
                signal_drop_counts={"DATA_INSUFFICIENT_MISSING_BBO": len(markets)},
                max_raw_edge_bps=max_raw_edge_bps,
                min_spread_bps=(0.0 if min_spread_bps == 10_000.0 else min_spread_bps),
                structural_signal_counts=structural_signal_counts,
                bbo_markets=bbo_markets,
                top_edge_market_debug=top_edge_market_debug,
                edge_calc_attempted=edge_calc_attempted,
                edge_calc_success=edge_calc_success,
                edge_calc_failed=edge_calc_failed,
                edge_calc_skipped=edge_calc_skipped,
                edge_calc_errors=edge_calc_errors,
                p50_edge_bps=p50_edge_bps,
                p90_edge_bps=p90_edge_bps,
                p99_edge_bps=p99_edge_bps,
                max_edge_bps=max_edge_bps,
                top3_edges=top3_edges[:3],
            )

        timing_ms["quote_phase"] = int((time.time() - phase1_started) * 1000)

        markets_by_id = {str(m.get("market_id")): m for m in markets}
        edge_stats = self.compute_edges_after_bbo(markets_by_id, quote_by_market_id, bbo_market_ids)
        edge_calc_attempted = int(edge_stats["edge_calc_attempted"])
        edge_calc_success = int(edge_stats["edge_calc_success"])
        edge_calc_failed = int(edge_stats["edge_calc_failed"])
        edge_calc_skipped = int(edge_stats["edge_calc_skipped"])
        edge_calc_errors = list(edge_stats["edge_calc_errors"])
        p50_edge_bps = float(edge_stats["p50_edge_bps"])
        p90_edge_bps = float(edge_stats["p90_edge_bps"])
        p99_edge_bps = float(edge_stats["p99_edge_bps"])
        max_edge_bps = float(edge_stats["max_edge_bps"])
        max_raw_edge_bps = float(edge_stats["max_raw_edge_bps"])
        min_spread_bps = float(edge_stats["min_spread_bps"])
        top3_edges = list(edge_stats["top3_edges"])
        top_edge_market_debug = dict(edge_stats["top_edge_market_debug"])
        structural_signal_counts["pair_cost_hit"] = int(edge_stats.get("pair_cost_hit_count", 0))

        if bbo_markets > 0 and edge_calc_attempted == 0:
            raise RuntimeError("EDGE_STAGE_NOT_EXECUTED")

        enable_cross_term = bbo_markets >= bbo_full_threshold
        mm_result = self._run_market_making_mode(
            markets=markets,
            quote_by_market_id=quote_by_market_id,
            arbitrage_markets=arbitrage_markets,
        )
        if not bool(mm_result.get("executed", False)):
            raise RuntimeError("MM_STAGE_NOT_RUN")
        orders_submitted += int(mm_result.get("orders_submitted", 0))
        mm_risk_status = str(mm_result.get("risk_status", "NORMAL"))
        arb_max_orders = int(self.config.get("market_making", {}).get("arb_max_orders_per_cycle", 1))
        arb_orders_submitted = 0

        # Phase 2: signal generation + risk + execution.
        phase2_started = time.time()
        for market in markets:
            if mm_risk_status == "HALTED":
                break
            if time.time() - cycle_started > max_cycle_sec:
                self.db.insert_audit_event(
                    level="WARN",
                    category="signal",
                    code="CYCLE_TIME_BUDGET_EXCEEDED_SIGNAL_PHASE",
                    message="signal/execution phase stopped by time budget",
                    context={"max_cycle_runtime_sec": max_cycle_sec, "signals_generated": signals_generated},
                )
                self._alert(
                    "WARN",
                    "Cycle runtime budget exceeded (signal phase)",
                    "signal/execution phase exited early to avoid watchdog kill",
                    context={"max_cycle_runtime_sec": max_cycle_sec, "signals_generated": signals_generated},
                )
                self._bump_run_stats("run_aborted")
                break

            quote = quote_by_market_id.get(market["market_id"])
            if quote is None:
                continue
            spread_now = float(quote.get("spread_bps", 0.0) or 0.0)
            if not market["tradable"]:
                continue

            peers = self.db.get_peer_markets(
                market_id=market["market_id"],
                event_id=market.get("event_id"),
            )
            debug: dict[str, Any] = {}
            try:
                signal = self.signal_engine.generate(
                    market=market,
                    quote=quote,
                    strategy_version="v0.1.0",
                    peer_markets=peers,
                    peer_quotes_by_market_id=quote_by_market_id,
                    debug=debug,
                    enable_cross_market_signal=enable_cross_term,
                    enable_term_structure_signal=enable_cross_term,
                )
            except TypeError:
                # Backward compatibility for monkeypatched tests/stubs with old signature.
                signal = self.signal_engine.generate(
                    market=market,
                    quote=quote,
                    strategy_version="v0.1.0",
                    peer_markets=peers,
                    debug=debug,
                )
            if debug.get("cross_market_hit"):
                structural_signal_counts["cross_market_hit"] += 1
            if debug.get("term_structure_hit"):
                structural_signal_counts["term_structure_hit"] += 1

            if not signal:
                reason = str(debug.get("drop_reason") or "UNKNOWN")
                signal_drop_counts[reason] = signal_drop_counts.get(reason, 0) + 1
                reject_counts[reason] = reject_counts.get(reason, 0) + 1
                self.db.insert_audit_event(
                    level="INFO",
                    category="signal",
                    code="SIGNAL_DROP",
                    message=reason,
                    context={
                        "market_id": market["market_id"],
                        "event_id": market.get("event_id"),
                        "drop_reason": reason,
                        "drop_category": debug.get("drop_category"),
                        "raw_edge_bps": debug.get("raw_edge_bps"),
                        "expected_edge_after_costs_bps": debug.get("expected_edge_after_costs_bps"),
                        "threshold_bps": debug.get("threshold_bps"),
                        "confidence": debug.get("confidence"),
                        "cost_breakdown": debug.get("cost_breakdown"),
                        "model_degraded": debug.get("model_degraded"),
                    },
                )
                emit_signal_decision(
                    self.logger,
                    signal_id=None,
                    market_id=market["market_id"],
                    raw_edge_bps=debug.get("raw_edge_bps"),
                    net_edge_bps=debug.get("expected_edge_after_costs_bps"),
                    spread_bps=quote.get("spread_bps", 0),
                    fee_bps=200,
                    slippage_bps=40,
                    confidence=debug.get("confidence"),
                    reject_reason=reason,
                    risk_block_reason=None,
                    gate_decision="REJECT",
                    execution_attempted=False,
                )
                continue

            if bool(signal.get("model_degraded", False)):
                llm_degraded_signals += 1

            signal_type = str(signal.get("signal_type", ""))
            if signal_type not in {"pair_cost_arb", "cross_market_divergence", "term_structure_inversion"}:
                continue
            arb_min_edge_bps = float(self.config.get("market_making", {}).get("arb_min_edge_bps", 200))
            if float(signal.get("expected_edge_after_costs_bps", 0.0) or 0.0) < arb_min_edge_bps:
                continue
            if arb_orders_submitted >= max(0, arb_max_orders):
                continue

            token_ids = market.get("clob_token_ids") or []
            if isinstance(token_ids, list) and token_ids:
                if signal.get("side") == "buy_no" and len(token_ids) >= 2:
                    signal["token_id"] = str(token_ids[1])
                else:
                    signal["token_id"] = str(token_ids[0])

            arbitrage_markets.add(str(signal.get("market_id")))
            self.db.insert_signal(signal)
            for pred in signal.get("predictions", []):
                self.db.insert_prediction(signal["signal_id"], pred)
            signals_generated += 1
            edge_now = float(signal.get("expected_edge_after_costs_bps", 0.0) or 0.0)
            top_signal_edges.append(edge_now)
            top_signal_edges = sorted(top_signal_edges, reverse=True)[:3]
            self.logger.log("SIGNAL_GENERATED", market_id=signal["market_id"], signal_id=signal["signal_id"])

            if signals_generated > 5 and edge_now < min(top_signal_edges):
                reject_counts["TOP3_ONLY"] = reject_counts.get("TOP3_ONLY", 0) + 1
                emit_signal_decision(
                    self.logger,
                    signal_id=signal["signal_id"],
                    market_id=signal["market_id"],
                    raw_edge_bps=signal.get("edge_bps"),
                    net_edge_bps=signal.get("expected_edge_after_costs_bps"),
                    spread_bps=quote.get("spread_bps", 0),
                    fee_bps=200,
                    slippage_bps=40,
                    confidence=signal.get("confidence"),
                    reject_reason="TOP3_ONLY",
                    risk_block_reason=None,
                    gate_decision="REJECT",
                    execution_attempted=False,
                )
                continue

            if self.db.has_active_order_for_signal(signal["signal_id"]):
                self.logger.log("IDEMPOTENT_SKIP", signal_id=signal["signal_id"])
                continue

            risk_snapshot = self.db.build_risk_snapshot(
                market_id=signal["market_id"],
                event_id=market.get("event_id"),
                quote=quote,
                account_equity_usd=float(self.config.get("risk", {}).get("account_equity_usd", 10_000)),
            )

            if bool(signal.get("model_degraded", False)):
                if not self._is_close_only_action(signal, risk_snapshot):
                    self.db.insert_audit_event(
                        level="WARN",
                        category="execution",
                        code="LLM_DEGRADED_OPEN_BLOCKED",
                        message="LLM unavailable/degraded; only close-only actions are allowed",
                        context={
                            "signal_id": signal["signal_id"],
                            "side": signal.get("side"),
                            "yes_qty": risk_snapshot.get("yes_qty", 0.0),
                            "no_qty": risk_snapshot.get("no_qty", 0.0),
                        },
                    )
                    self.logger.log(
                        "LLM_DEGRADED_OPEN_BLOCKED",
                        signal_id=signal["signal_id"],
                        side=signal.get("side"),
                    )
                    continue

            intent = self.execution.build_order_intent(signal=signal, quote=quote, market=market, risk_snapshot=risk_snapshot)
            decision = self.risk.check(intent)
            if not decision.approved:
                code = str(decision.reject_code or "RISK_REJECT")
                reject_counts[code] = reject_counts.get(code, 0) + 1
                self.db.insert_audit_event(
                    level="WARN",
                    category="risk",
                    code=code,
                    message=decision.message,
                    context={
                        "signal_id": signal["signal_id"],
                        "risk_snapshot": risk_snapshot,
                        "risk_details": decision.details or {},
                        "intent": intent,
                    },
                )
                emit_signal_decision(
                    self.logger,
                    signal_id=signal["signal_id"],
                    market_id=signal["market_id"],
                    raw_edge_bps=signal.get("edge_bps"),
                    net_edge_bps=signal.get("expected_edge_after_costs_bps"),
                    spread_bps=quote.get("spread_bps", 0),
                    fee_bps=200,
                    slippage_bps=40,
                    confidence=signal.get("confidence"),
                    reject_reason=code,
                    risk_block_reason=code,
                    gate_decision="REJECT",
                    execution_attempted=False,
                )
                self.logger.log(
                    "RISK_REJECT",
                    signal_id=signal["signal_id"],
                    code=decision.reject_code,
                    message=decision.message,
                )
                self._alert(
                    "WARN",
                    "Risk reject",
                    f"{decision.reject_code}: {decision.message}",
                    context={"signal_id": signal["signal_id"], "details": decision.details or {}},
                )
                continue

            limit_price, quantity = self.execution.build_order(signal=signal, quote=quote)
            order_res, execution_attempted = self.execution.execute_order(signal=signal, quote=quote)
            order = self.execution.order_record(
                order_id=order_res.order_id,
                signal=signal,
                limit_price=limit_price,
                quantity=quantity,
                status=order_res.status,
                retries=order_res.retries,
            )
            self.db.insert_order(order)
            orders_submitted += 1
            arb_orders_submitted += 1
            if order_res.status in {"rejected", "canceled"}:
                reject_counts[str(order_res.reason)] = reject_counts.get(str(order_res.reason), 0) + 1
            emit_signal_decision(
                self.logger,
                signal_id=signal["signal_id"],
                market_id=signal["market_id"],
                raw_edge_bps=signal.get("edge_bps"),
                net_edge_bps=signal.get("expected_edge_after_costs_bps"),
                spread_bps=quote.get("spread_bps", 0),
                fee_bps=200,
                slippage_bps=40,
                confidence=signal.get("confidence"),
                reject_reason=(order_res.reason if order_res.status in {"rejected", "canceled"} else None),
                risk_block_reason=None,
                gate_decision=("ALLOW" if execution_attempted else "REJECT"),
                execution_attempted=execution_attempted,
            )
            if order_res.status in {"rejected", "canceled"}:
                self._alert(
                    "CRITICAL" if order_res.status == "rejected" else "WARN",
                    "Order non-fill terminal state",
                    f"status={order_res.status}, reason={order_res.reason}",
                    context={"order_id": order_res.order_id, "signal_id": signal["signal_id"]},
                )

            self.logger.log(
                "ORDER_SUBMITTED",
                market_id=signal["market_id"],
                signal_id=signal["signal_id"],
                order_id=order_res.order_id,
                limit_price=limit_price,
                quantity=quantity,
            )

            paper_rows = evaluate_signal_horizons(
                signal=signal,
                current_quote=quote,
                future_quotes=self.db.get_future_quotes(signal["market_id"], quote["ts_utc"]),
                horizons_min=self.config["evaluate"]["paper_horizons_min"],
            )
            for row in paper_rows:
                self.db.insert_paper_result(row)

        timing_ms["signal_execution_phase"] = int((time.time() - phase2_started) * 1000)
        timing_ms["total"] = int((time.time() - cycle_started) * 1000)

        llm_pause_threshold = int(self.config.get("ops", {}).get("llm_degrade_pause_threshold", 999999))
        if mm_risk_status == "HALTED":
            self.state.transition("HALTED", "RISK_HALT_DAILY_DRAWDOWN")
        elif llm_degraded_signals >= llm_pause_threshold:
            self.state.transition("PAUSED", "llm_degraded_threshold")
            self.db.insert_audit_event(
                level="WARN",
                category="ops",
                code="LLM_DEGRADED_THRESHOLD_PAUSE",
                message="paused due to excessive degraded-model signals in one cycle",
                context={"llm_degraded_signals": llm_degraded_signals, "threshold": llm_pause_threshold},
            )
            self._alert(
                "WARN",
                "Paused: LLM degraded threshold reached",
                "Too many degraded-model signals in one cycle",
                context={"llm_degraded_signals": llm_degraded_signals, "threshold": llm_pause_threshold},
            )
        else:
            self.state.transition("RUNNING", "cycle_completed")

        self.db.insert_audit_event(
            level="INFO",
            category="pipeline",
            code="RUN_ONCE_TIMING",
            message="run_once_timing",
            context={"timing_ms": timing_ms, "llm_degraded_signals": llm_degraded_signals},
        )

        if edge_calc_attempted < bbo_markets:
            self.logger.log(
                "EDGE_CALC_WARN",
                bbo_markets=bbo_markets,
                edge_calc_attempted=edge_calc_attempted,
                uncalculated_count=(bbo_markets-edge_calc_attempted),
                reason={"failed": edge_calc_failed, "skipped": edge_calc_skipped},
            )

        if signals_generated > 0 and orders_submitted == 0:
            summary_keys = [
                "NET_EDGE_TOO_LOW",
                "SPREAD_TOO_WIDE",
                "CLUSTER_EXPOSURE_LIMIT",
                "PER_MARKET_LIMIT",
                "DRAWDOWN_LIMIT",
                "LLM_CONFIDENCE_LOW",
            ]
            normalized = {k: int(reject_counts.get(k, 0)) for k in summary_keys}
            normalized["OTHER"] = int(sum(v for k, v in reject_counts.items() if k not in summary_keys))
            emit_reject_summary(self.logger, normalized)

        self._bump_run_stats("run_completed")
        return PipelineResult(
            scanned_markets=len(markets),
            quotes_written=quotes_written,
            signals_generated=signals_generated,
            orders_submitted=orders_submitted,
            signal_drop_counts=signal_drop_counts,
            max_raw_edge_bps=max_raw_edge_bps,
            min_spread_bps=(0.0 if min_spread_bps == 10_000.0 else min_spread_bps),
            structural_signal_counts=structural_signal_counts,
            bbo_markets=bbo_markets,
            top_edge_market_debug=top_edge_market_debug,
            edge_calc_attempted=edge_calc_attempted,
            edge_calc_success=edge_calc_success,
            edge_calc_failed=edge_calc_failed,
            edge_calc_skipped=edge_calc_skipped,
            edge_calc_errors=edge_calc_errors,
            p50_edge_bps=p50_edge_bps,
            p90_edge_bps=p90_edge_bps,
            p99_edge_bps=p99_edge_bps,
            max_edge_bps=max_edge_bps,
            top3_edges=top3_edges[:3],
            baseline_mode=True,
            active_mm_markets=list(mm_result.get("active_mm_markets", [])),
            active_arbitrage_markets=sorted(arbitrage_markets),
            inventory_snapshot=dict(mm_result.get("inventory_snapshot", {})),
            mm_stage_executed=bool(mm_result.get("executed", False)),
            mm_candidates_count=int(mm_result.get("mm_candidates_count", 0)),
            mm_selected_count=int(mm_result.get("mm_selected_count", 0)),
            mm_reject_counts=dict(mm_result.get("mm_reject_counts", {})),
            mm_sample_rejects=list(mm_result.get("mm_sample_rejects", [])),
            total_inventory=float(mm_result.get("total_inventory", 0.0)),
            total_inventory_net=float(mm_result.get("total_inventory_net", 0.0)),
            total_exposure_ratio=float(mm_result.get("total_exposure_ratio", 0.0)),
            daily_pnl=float(mm_result.get("daily_pnl", 0.0)),
            risk_status=str(mm_result.get("risk_status", "NORMAL")),
        )
