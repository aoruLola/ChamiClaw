from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from chamiclaw.utils.time import utc_now_iso


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self, schema_path: str = "sql/schema.sql") -> None:
        with self.connect() as conn:
            with open(schema_path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())

    def upsert_market(self, market: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO markets (
                  market_id, event_id, slug, question, description,
                  end_time_utc, liquidity_usd, volume_usd,
                  rule_summary_json, tradable, tradable_reason, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_id) DO UPDATE SET
                  event_id=excluded.event_id,
                  slug=excluded.slug,
                  question=excluded.question,
                  description=excluded.description,
                  end_time_utc=excluded.end_time_utc,
                  liquidity_usd=excluded.liquidity_usd,
                  volume_usd=excluded.volume_usd,
                  rule_summary_json=excluded.rule_summary_json,
                  tradable=excluded.tradable,
                  tradable_reason=excluded.tradable_reason,
                  updated_at_utc=excluded.updated_at_utc
                """,
                (
                    market["market_id"],
                    market.get("event_id"),
                    market.get("slug"),
                    market.get("question", ""),
                    market.get("description", ""),
                    market.get("end_time_utc"),
                    float(market.get("liquidity_usd", 0) or 0),
                    float(market.get("volume_usd", 0) or 0),
                    json.dumps(market.get("rule_summary", {}), ensure_ascii=True),
                    int(bool(market.get("tradable", False))),
                    market.get("tradable_reason", ""),
                    utc_now_iso(),
                ),
            )

    def insert_quote(self, quote: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO quotes (
                  market_id, ts_utc,
                  yes_bid, yes_ask, no_bid, no_ask,
                  yes_mid, no_mid, spread_bps,
                  depth_usd, depth_imbalance, sigma_5m,
                  raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quote["market_id"],
                    quote["ts_utc"],
                    quote.get("yes_bid"),
                    quote.get("yes_ask"),
                    quote.get("no_bid"),
                    quote.get("no_ask"),
                    quote.get("yes_mid"),
                    quote.get("no_mid"),
                    quote.get("spread_bps"),
                    quote.get("depth_usd"),
                    quote.get("depth_imbalance"),
                    quote.get("sigma_5m"),
                    json.dumps(quote.get("raw", {}), ensure_ascii=True),
                ),
            )

    def insert_signal(self, signal: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO signals (
                  signal_id, market_id, strategy_version, signal_type, side,
                  market_prob, fair_prob, edge_bps, expected_edge_after_costs_bps,
                  confidence, reason, status, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal["signal_id"],
                    signal["market_id"],
                    signal["strategy_version"],
                    signal["signal_type"],
                    signal["side"],
                    signal["market_prob"],
                    signal.get("fair_prob"),
                    signal.get("edge_bps"),
                    signal.get("expected_edge_after_costs_bps"),
                    signal.get("confidence"),
                    signal.get("reason", ""),
                    signal.get("status", "generated"),
                    signal["created_at_utc"],
                ),
            )

    def insert_prediction(self, signal_id: str, prediction: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO predictions (
                  signal_id, model_name, fair_prob, confidence,
                  rationale, risk_tags_json, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    prediction.get("model_name", "unknown"),
                    prediction.get("fair_prob"),
                    prediction.get("confidence"),
                    prediction.get("rationale", ""),
                    json.dumps(prediction.get("risk_tags", []), ensure_ascii=True),
                    utc_now_iso(),
                ),
            )

    def insert_order(self, order: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO orders (
                  order_id, signal_id, market_id, side,
                  limit_price, quantity, status, retries,
                  created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order["order_id"],
                    order.get("signal_id"),
                    order["market_id"],
                    order["side"],
                    order["limit_price"],
                    order["quantity"],
                    order["status"],
                    order.get("retries", 0),
                    order["created_at_utc"],
                    order["updated_at_utc"],
                ),
            )

    def has_active_order_for_signal(self, signal_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM orders
                WHERE signal_id=? AND status IN ('submitted', 'partial', 'new')
                LIMIT 1
                """,
                (signal_id,),
            ).fetchone()
        return row is not None

    def insert_paper_result(self, row: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO paper_results (
                  signal_id, market_id, horizon_min, entry_prob, exit_prob,
                  realized_edge_bps, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("signal_id"),
                    row["market_id"],
                    row["horizon_min"],
                    row["entry_prob"],
                    row.get("exit_prob"),
                    row.get("realized_edge_bps"),
                    row["created_at_utc"],
                ),
            )

    def list_tradable_markets(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM markets
                WHERE tradable = 1
                ORDER BY liquidity_usd DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_last_quotes(self, market_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM quotes WHERE market_id=?
                ORDER BY ts_utc DESC
                LIMIT ?
                """,
                (market_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_quote(self, market_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM quotes
                WHERE market_id=?
                ORDER BY ts_utc DESC
                LIMIT 1
                """,
                (market_id,),
            ).fetchone()
        return dict(row) if row else None

    def insert_audit_event(self, level: str, category: str, code: str, message: str, context: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events (ts_utc, level, category, code, message, context_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (utc_now_iso(), level, category, code, message, json.dumps(context or {}, ensure_ascii=True)),
            )

    def build_risk_snapshot(
        self,
        market_id: str,
        event_id: str | None,
        quote: dict[str, Any],
        account_equity_usd: float,
    ) -> dict[str, Any]:
        account_equity = max(1.0, float(account_equity_usd or 1.0))
        yes_mid = float(quote.get("yes_mid") or 0.0)
        no_mid = float(quote.get("no_mid") or 0.0)

        with self.connect() as conn:
            pos = conn.execute(
                """
                SELECT yes_qty, no_qty FROM positions WHERE market_id=?
                """,
                (market_id,),
            ).fetchone()
            yes_qty = float(pos["yes_qty"]) if pos else 0.0
            no_qty = float(pos["no_qty"]) if pos else 0.0
            market_notional = yes_qty * yes_mid + no_qty * no_mid

            open_orders_same_market = conn.execute(
                """
                SELECT COUNT(*) AS c FROM orders
                WHERE market_id=? AND status IN ('new', 'submitted', 'partial')
                """,
                (market_id,),
            ).fetchone()["c"]

            cluster_notional = market_notional
            if event_id:
                rows = conn.execute(
                    """
                    SELECT
                      m.market_id,
                      COALESCE(p.yes_qty, 0) AS yes_qty,
                      COALESCE(p.no_qty, 0) AS no_qty,
                      (
                        SELECT q.yes_mid
                        FROM quotes q
                        WHERE q.market_id = m.market_id
                        ORDER BY q.ts_utc DESC
                        LIMIT 1
                      ) AS yes_mid,
                      (
                        SELECT q.no_mid
                        FROM quotes q
                        WHERE q.market_id = m.market_id
                        ORDER BY q.ts_utc DESC
                        LIMIT 1
                      ) AS no_mid
                    FROM markets m
                    LEFT JOIN positions p ON p.market_id = m.market_id
                    WHERE m.event_id = ?
                    """,
                    (event_id,),
                ).fetchall()
                cluster_notional = 0.0
                for r in rows:
                    row_yes_mid = yes_mid if r["market_id"] == market_id else float(r["yes_mid"] or 0.0)
                    row_no_mid = no_mid if r["market_id"] == market_id else float(r["no_mid"] or 0.0)
                    cluster_notional += float(r["yes_qty"]) * row_yes_mid + float(r["no_qty"]) * row_no_mid

            day_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
            pnl_row = conn.execute(
                """
                SELECT COALESCE(SUM(pnl_usd), 0) AS pnl
                FROM trades
                WHERE ts_utc >= ?
                """,
                (day_start,),
            ).fetchone()
            daily_pnl = float(pnl_row["pnl"] or 0.0)

        return {
            "position_pct": market_notional / account_equity,
            "cluster_exposure_pct": cluster_notional / account_equity,
            "daily_drawdown_pct": max(0.0, -daily_pnl / account_equity),
            "open_orders_same_market": int(open_orders_same_market),
            "market_notional_usd": market_notional,
            "cluster_notional_usd": cluster_notional,
            "daily_realized_pnl_usd": daily_pnl,
        }

    def get_peer_markets(self, market_id: str, event_id: str | None, limit: int = 20) -> list[dict[str, Any]]:
        if not event_id:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT market_id, event_id, end_time_utc, liquidity_usd
                FROM markets
                WHERE event_id=? AND market_id<>?
                ORDER BY liquidity_usd DESC
                LIMIT ?
                """,
                (event_id, market_id, limit),
            ).fetchall()
        peers = [dict(r) for r in rows]
        for peer in peers:
            q = self.get_latest_quote(peer["market_id"])
            if q:
                peer["outcome_prices"] = [q.get("yes_mid", 0.5), q.get("no_mid", 0.5)]
        return peers

    def get_future_quotes(self, market_id: str, after_ts_utc: str, limit: int = 3000) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM quotes
                WHERE market_id=? AND ts_utc>=?
                ORDER BY ts_utc ASC
                LIMIT ?
                """,
                (market_id, after_ts_utc, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def insert_strategy_version(self, strategy_version: str, config_snapshot: dict[str, Any], notes: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO strategy_versions (
                  strategy_version, created_at_utc, config_snapshot_json, notes
                ) VALUES (?, ?, ?, ?)
                """,
                (strategy_version, utc_now_iso(), json.dumps(config_snapshot, ensure_ascii=True), notes),
            )

    def get_latest_strategy_version(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT strategy_version, created_at_utc, config_snapshot_json, notes
                FROM strategy_versions
                ORDER BY created_at_utc DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["config_snapshot"] = json.loads(data.get("config_snapshot_json") or "{}")
        except json.JSONDecodeError:
            data["config_snapshot"] = {}
        return data

    def upsert_position(self, market_id: str, yes_qty: float, no_qty: float) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO positions (market_id, yes_qty, no_qty, updated_at_utc)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(market_id) DO UPDATE SET
                  yes_qty=excluded.yes_qty,
                  no_qty=excluded.no_qty,
                  updated_at_utc=excluded.updated_at_utc
                """,
                (market_id, yes_qty, no_qty, utc_now_iso()),
            )

    def get_go_no_go_snapshot(self) -> dict[str, Any]:
        with self.connect() as conn:
            duplicate_order_signals = conn.execute(
                """
                SELECT COUNT(*) AS c FROM (
                  SELECT signal_id
                  FROM orders
                  WHERE signal_id IS NOT NULL
                  GROUP BY signal_id
                  HAVING COUNT(*) > 1
                )
                """
            ).fetchone()["c"]

            edge_violation_orders = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM orders o
                JOIN signals s ON s.signal_id = o.signal_id
                WHERE COALESCE(s.expected_edge_after_costs_bps, 0) <= 0
                """
            ).fetchone()["c"]

            total_risk_rejects = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM audit_events
                WHERE category='risk' AND code <> 'SCAN_FAIL'
                """
            ).fetchone()["c"]
            risk_rows = conn.execute(
                """
                SELECT context_json
                FROM audit_events
                WHERE category='risk'
                """
            ).fetchall()

            risk_reject_complete = 0
            for r in risk_rows:
                try:
                    ctx = json.loads(r["context_json"] or "{}")
                except json.JSONDecodeError:
                    ctx = {}
                if isinstance(ctx, dict) and ctx.get("risk_details") and ctx.get("intent"):
                    risk_reject_complete += 1

            reconcile_rows = conn.execute(
                """
                SELECT ts_utc, level, message, context_json
                FROM audit_events
                WHERE category='reconcile' AND code='RECONCILE_RESULT'
                ORDER BY ts_utc DESC
                LIMIT 50
                """
            ).fetchall()
            reconcile_recent_total = len(reconcile_rows)
            reconcile_recent_bad = 0
            for row in reconcile_rows:
                msg = str(row["message"] or "")
                mismatch = 0
                if "mismatch_count=" in msg:
                    try:
                        mismatch = int(msg.split("mismatch_count=", 1)[1].split(",", 1)[0])
                    except ValueError:
                        mismatch = 0
                if mismatch > 0 or str(row["level"]) in {"WARN", "ERROR"}:
                    reconcile_recent_bad += 1

            llm_error_preds = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM predictions
                WHERE model_name='llm_error'
                """
            ).fetchone()["c"]
            llm_total_preds = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM predictions
                WHERE model_name IN ('llm1', 'llm2', 'llm_error')
                """
            ).fetchone()["c"]

            run_once_rows = conn.execute(
                """
                SELECT context_json
                FROM audit_events
                WHERE category='pipeline' AND code='RUN_ONCE_SUMMARY'
                ORDER BY ts_utc DESC
                LIMIT 20
                """
            ).fetchall()
            recent_run_once_cycles = len(run_once_rows)
            recent_signals_generated = 0
            for row in run_once_rows:
                try:
                    ctx = json.loads(row["context_json"] or "{}")
                except json.JSONDecodeError:
                    ctx = {}
                recent_signals_generated += int(ctx.get("signals_generated", 0) or 0)

            edge_rows = conn.execute(
                """
                SELECT expected_edge_after_costs_bps
                FROM signals
                ORDER BY created_at_utc DESC
                LIMIT 200
                """
            ).fetchall()
            edge_values: list[float] = []
            for row in edge_rows:
                try:
                    edge_values.append(float(row["expected_edge_after_costs_bps"] or 0.0))
                except (TypeError, ValueError):
                    continue

            # If accepted-signal sample is too small, use recent signal-drop audit contexts
            # to estimate opportunity quality under current thresholds.
            if len(edge_values) < 20:
                drop_rows = conn.execute(
                    """
                    SELECT context_json
                    FROM audit_events
                    WHERE category='signal' AND code='SIGNAL_DROP'
                    ORDER BY ts_utc DESC
                    LIMIT 300
                    """
                ).fetchall()
                for row in drop_rows:
                    try:
                        ctx = json.loads(row["context_json"] or "{}")
                    except json.JSONDecodeError:
                        ctx = {}
                    if not isinstance(ctx, dict):
                        continue
                    v = ctx.get("expected_edge_after_costs_bps")
                    if v is None:
                        continue
                    try:
                        edge_values.append(float(v))
                    except (TypeError, ValueError):
                        continue

            edge_sample_count = len(edge_values)
            edge_positive_after_cost_count = len([v for v in edge_values if v > 0])
            edge_positive_after_cost_ratio = (
                float(edge_positive_after_cost_count) / float(edge_sample_count) if edge_sample_count > 0 else 0.0
            )

        return {
            "duplicate_order_signals": int(duplicate_order_signals),
            "edge_violation_orders": int(edge_violation_orders),
            "total_risk_rejects": int(total_risk_rejects),
            "risk_reject_complete": int(risk_reject_complete),
            "reconcile_recent_total": int(reconcile_recent_total),
            "reconcile_recent_bad": int(reconcile_recent_bad),
            "llm_error_preds": int(llm_error_preds),
            "llm_total_preds": int(llm_total_preds),
            "recent_run_once_cycles": int(recent_run_once_cycles),
            "recent_signals_generated": int(recent_signals_generated),
            "edge_sample_count": int(edge_sample_count),
            "edge_positive_after_cost_count": int(edge_positive_after_cost_count),
            "edge_positive_after_cost_ratio": float(edge_positive_after_cost_ratio),
        }
