import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chamiclaw.app import ChamiClawApp
from chamiclaw.db.sqlite import Database
from chamiclaw.utils.json_logger import JsonLogger


class _ApprovedDecision:
    approved = True
    reject_code = None
    message = "ok"
    details = {}


class _OrderOut:
    def __init__(self, order_id: str = "o1", status: str = "submitted"):
        self.order_id = order_id
        self.status = status
        self.reason = "ok"
        self.retries = 0


class StableMmModeTest(unittest.TestCase):
    def _base_cfg(self):
        return {
            "apis": {
                "gamma_base": "https://gamma-api.polymarket.com",
                "clob_base": "https://clob.polymarket.com",
            },
            "risk": {
                "account_equity_usd": 10_000,
                "per_market_pos_pct": 0.5,
                "event_cluster_exposure_pct": 0.9,
                "daily_max_drawdown_pct": 0.5,
                "max_spread_bps": 2000,
                "pre_expiry_add_position_block_min": 0,
                "max_open_orders_per_market": 10,
            },
            "execution": {"dry_run": True},
            "market_making": {
                "enabled": False,
                "baseline_mode": True,
                "min_spread_bps": 40,
                "min_market_liquidity_usd": 100000,
                "min_time_to_expiry_min": 1440,
                "quote_offset_ratio": 0.20,
                "per_market_exposure_pct": 0.03,
                "total_exposure_pct": 0.20,
                "inventory_threshold_pct": 0.02,
                "pause_after_consecutive_losses": 2,
                "pause_minutes": 30,
                "mid_move_pause_bps_5m": 120,
                "depth_drop_ratio_warn": 0.50,
                "depth_drop_ratio_hard": 0.70,
                "drawdown_throttle_pct": 0.03,
                "drawdown_throttle_scale": 0.50,
                "drawdown_halt_pct": 0.05,
                "requote_min_sec": 30,
                "requote_max_sec": 60,
                "use_llm_fair_prob": True,
            },
            "llm": {"mode": "mock"},
        }

    def test_conservative_quote_uses_mid_without_llm_adjustment(self):
        cfg = self._base_cfg()
        end_time = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat().replace("+00:00", "Z")
        market = {
            "market_id": "m1",
            "event_id": "e1",
            "tradable": True,
            "liquidity_usd": 200000,
            "end_time_utc": end_time,
            "clob_token_ids": ["t_yes", "t_no"],
        }
        quote = {
            "yes_bid": 0.45,
            "yes_ask": 0.55,
            "yes_mid": 0.5,
            "no_mid": 0.5,
            "spread_bps": 2000,
            "depth_usd": 200000,
            "depth_imbalance": 0.0,
            "sigma_5m": 0.0,
        }

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "x.db"))
            db.init_schema("sql/schema.sql")
            app = ChamiClawApp(cfg, db, JsonLogger(str(Path(tmp) / "events.jsonl")))

            app.risk.check = lambda _intent: _ApprovedDecision()  # type: ignore[method-assign]
            app._load_mm_state = lambda: {"markets": {}}  # type: ignore[method-assign]
            app._save_mm_state = lambda _state: None  # type: ignore[method-assign]

            class _FailingLlm:
                def infer(self, market_prob, features):
                    raise AssertionError("LLM should not be used for MM pricing")

            app.signal_engine.llm1 = _FailingLlm()

            seen_prices = []

            def _place(signal, limit_price, quantity):
                seen_prices.append((signal["side"], float(limit_price), float(quantity)))
                return _OrderOut(order_id=f"o-{len(seen_prices)}")

            app.execution.place_limit_order = _place  # type: ignore[method-assign]

            out = app._run_market_making_mode(
                markets=[market],
                quote_by_market_id={"m1": quote},
                arbitrage_markets=set(),
            )

        self.assertTrue(out["executed"])
        by_side = {x[0]: x[1] for x in seen_prices}
        self.assertAlmostEqual(by_side["buy_yes"], 0.48, places=6)

    def test_filters_market_near_expiry(self):
        cfg = self._base_cfg()
        end_time = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        market = {
            "market_id": "m1",
            "event_id": "e1",
            "tradable": True,
            "liquidity_usd": 200000,
            "end_time_utc": end_time,
            "clob_token_ids": ["t_yes", "t_no"],
        }
        quote = {
            "yes_bid": 0.45,
            "yes_ask": 0.55,
            "yes_mid": 0.5,
            "no_mid": 0.5,
            "spread_bps": 2000,
            "depth_usd": 200000,
            "depth_imbalance": 0.0,
            "sigma_5m": 0.0,
        }

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "x.db"))
            db.init_schema("sql/schema.sql")
            app = ChamiClawApp(cfg, db, JsonLogger(str(Path(tmp) / "events.jsonl")))
            app.risk.check = lambda _intent: _ApprovedDecision()  # type: ignore[method-assign]
            app._load_mm_state = lambda: {"markets": {}}  # type: ignore[method-assign]
            app._save_mm_state = lambda _state: None  # type: ignore[method-assign]
            called = {"n": 0}

            def _place(signal, limit_price, quantity):
                called["n"] += 1
                return _OrderOut(order_id=f"o-{called['n']}")

            app.execution.place_limit_order = _place  # type: ignore[method-assign]
            out = app._run_market_making_mode(
                markets=[market],
                quote_by_market_id={"m1": quote},
                arbitrage_markets=set(),
            )

        self.assertEqual(called["n"], 0)
        self.assertEqual(out["orders_submitted"], 0)
        self.assertGreaterEqual(int(out["mm_reject_counts"].get("MM_EXPIRY_TOO_NEAR", 0)), 1)

    def test_halts_market_making_when_daily_drawdown_exceeds_limit(self):
        cfg = self._base_cfg()
        end_time = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat().replace("+00:00", "Z")
        market = {
            "market_id": "m1",
            "event_id": "e1",
            "tradable": True,
            "liquidity_usd": 200000,
            "end_time_utc": end_time,
            "clob_token_ids": ["t_yes", "t_no"],
            "slug": "s1",
            "question": "q1",
            "description": "",
            "volume_usd": 1000,
            "rule_summary": {},
            "tradable_reason": "ok",
        }
        quote = {
            "yes_bid": 0.45,
            "yes_ask": 0.55,
            "yes_mid": 0.5,
            "no_mid": 0.5,
            "spread_bps": 2000,
            "depth_usd": 200000,
            "depth_imbalance": 0.0,
            "sigma_5m": 0.0,
            "ts_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "x.db"))
            db.init_schema("sql/schema.sql")
            db.upsert_market(market)
            db.insert_quote(
                {
                    "market_id": "m1",
                    "ts_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "yes_bid": 0.45,
                    "yes_ask": 0.55,
                    "no_bid": 0.45,
                    "no_ask": 0.55,
                    "yes_mid": 0.5,
                    "no_mid": 0.5,
                    "spread_bps": 2000,
                    "depth_usd": 200000,
                    "depth_imbalance": 0.0,
                    "sigma_5m": 0.0,
                    "raw": {},
                }
            )
            with db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO trades (trade_id, order_id, market_id, side, fill_price, fill_qty, fee_usd, slippage_bps, pnl_usd, ts_utc)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("t1", None, "m1", "buy_yes", 0.5, 10.0, 0.0, 0.0, -600.0, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
                )
            app = ChamiClawApp(cfg, db, JsonLogger(str(Path(tmp) / "events.jsonl")))
            app._load_mm_state = lambda: {"markets": {}}  # type: ignore[method-assign]
            app._save_mm_state = lambda _state: None  # type: ignore[method-assign]
            app.state.transition = lambda _target, _reason: None  # type: ignore[method-assign]

            out = app._run_market_making_mode(
                markets=[market],
                quote_by_market_id={"m1": quote},
                arbitrage_markets=set(),
            )

        self.assertEqual(out["risk_status"], "HALTED")
        self.assertEqual(out["orders_submitted"], 0)


if __name__ == "__main__":
    unittest.main()
