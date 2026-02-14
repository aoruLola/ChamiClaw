from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from chamiclaw.db.sqlite import Database
from chamiclaw.evaluate.paper import evaluate_signal_horizons
from chamiclaw.execution.executor import ExecutionEngine
from chamiclaw.ops.alerting import post_discord_alert
from chamiclaw.ops.state_machine import SystemStateMachine
from chamiclaw.risk.engine import RiskEngine
from chamiclaw.scanner.clob_client import CLOBClient
from chamiclaw.scanner.gamma_client import GammaClient
from chamiclaw.scanner.market_scanner import scan_markets
from chamiclaw.scanner.quote_collector import build_quote
from chamiclaw.signal.engine import SignalEngine
from chamiclaw.utils.json_logger import JsonLogger


@dataclass
class PipelineResult:
    scanned_markets: int
    quotes_written: int
    signals_generated: int
    orders_submitted: int
    signal_drop_counts: dict[str, int]


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

    def run_once(self) -> PipelineResult:
        state = self.state.load()
        if state.state == "HALTED":
            self.logger.log("SYSTEM_HALTED", reason=state.reason)
            return PipelineResult(
                scanned_markets=0,
                quotes_written=0,
                signals_generated=0,
                orders_submitted=0,
                signal_drop_counts={},
            )

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
            return PipelineResult(
                scanned_markets=0,
                quotes_written=0,
                signals_generated=0,
                orders_submitted=0,
                signal_drop_counts={},
            )
        quotes_written = 0
        signals_generated = 0
        orders_submitted = 0
        signal_drop_counts: dict[str, int] = {}
        orderbook_calls = 0
        orderbook_budget = int(self.config.get("scan", {}).get("max_orderbook_calls_per_cycle", 40))
        orderbook_for_tradable_only = bool(self.config.get("scan", {}).get("orderbook_for_tradable_only", True))
        enable_orderbook = bool(self.config.get("scan", {}).get("enable_orderbook", True))
        cycle_started = time.time()
        max_cycle_sec = float(self.config.get("scan", {}).get("max_cycle_runtime_sec", 120))

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
                break
            self.db.upsert_market(market)
            recent = self.db.get_last_quotes(market["market_id"], limit=20)
            recent_yes = [float(x.get("yes_mid", 0)) for x in reversed(recent)]
            should_fetch_orderbook = enable_orderbook and orderbook_calls < orderbook_budget
            if orderbook_for_tradable_only and not market.get("tradable", False):
                should_fetch_orderbook = False
            orderbook = None
            if should_fetch_orderbook:
                orderbook = self.clob.fetch_top_of_book(
                    market_id=market["market_id"],
                    timeout_sec=float(self.config.get("scan", {}).get("orderbook_timeout_sec", 6)),
                )
                orderbook_calls += 1
            quote = build_quote(market, recent_yes, orderbook=orderbook)
            self.db.insert_quote(quote)
            quotes_written += 1

            if not market["tradable"]:
                continue

            peers = self.db.get_peer_markets(
                market_id=market["market_id"],
                event_id=market.get("event_id"),
            )
            debug: dict[str, Any] = {}
            signal = self.signal_engine.generate(
                market=market,
                quote=quote,
                strategy_version="v0.1.0",
                peer_markets=peers,
                debug=debug,
            )
            if not signal:
                reason = str(debug.get("drop_reason") or "UNKNOWN")
                signal_drop_counts[reason] = signal_drop_counts.get(reason, 0) + 1
                continue

            self.db.insert_signal(signal)
            for pred in signal.get("predictions", []):
                self.db.insert_prediction(signal["signal_id"], pred)
            signals_generated += 1
            self.logger.log("SIGNAL_GENERATED", market_id=signal["market_id"], signal_id=signal["signal_id"])

            if self.db.has_active_order_for_signal(signal["signal_id"]):
                self.logger.log("IDEMPOTENT_SKIP", signal_id=signal["signal_id"])
                continue

            risk_snapshot = self.db.build_risk_snapshot(
                market_id=signal["market_id"],
                event_id=market.get("event_id"),
                quote=quote,
                account_equity_usd=float(self.config.get("risk", {}).get("account_equity_usd", 10_000)),
            )
            intent = self.execution.build_order_intent(signal=signal, quote=quote, market=market, risk_snapshot=risk_snapshot)
            decision = self.risk.check(intent)
            if not decision.approved:
                self.db.insert_audit_event(
                    level="WARN",
                    category="risk",
                    code=decision.reject_code or "RISK_REJECT",
                    message=decision.message,
                    context={
                        "signal_id": signal["signal_id"],
                        "risk_snapshot": risk_snapshot,
                        "risk_details": decision.details or {},
                        "intent": intent,
                    },
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
            order_res = self.execution.place_limit_order(signal=signal, limit_price=limit_price, quantity=quantity)
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

        self.state.transition("RUNNING", "cycle_completed")

        return PipelineResult(
            scanned_markets=len(markets),
            quotes_written=quotes_written,
            signals_generated=signals_generated,
            orders_submitted=orders_submitted,
            signal_drop_counts=signal_drop_counts,
        )
