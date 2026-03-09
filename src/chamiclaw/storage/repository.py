from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from chamiclaw.core.models import (
    FillRecord,
    InfoSignal,
    MarketCard,
    ModeState,
    OptimizationTrial,
    OrderIntent,
    OrderRecord,
    ParamsVersion,
    PhaseGateState,
    PortfolioState,
    PriceSignal,
    PriceSnapshot,
    StrategyParams,
    TradeStats,
)
from chamiclaw.core.settings import AppSettings


class Repository(Protocol):
    markets: dict[str, MarketCard]
    price_snapshots: dict[str, PriceSnapshot]
    price_signals: dict[str, PriceSignal]
    info_signals: dict[str, InfoSignal]
    mode_states: dict[str, ModeState]
    trade_stats: TradeStats
    portfolio: PortfolioState
    phase_gate_state: PhaseGateState
    order_records: list[OrderRecord]
    fill_records: list[FillRecord]
    trade_logs: list[dict]
    positions_snapshots: list[PortfolioState]
    execution_compensations: dict[str, OrderIntent]
    strategy_params_current: ParamsVersion
    strategy_params_history: list[ParamsVersion]
    optimization_trials: list[OptimizationTrial]
    price_stream_running: bool
    price_stream_last_event_ts: datetime | None
    price_stream_reconnects: int

    def upsert_market(self, market: MarketCard) -> None: ...

    def replace_markets(self, markets: list[MarketCard]) -> None: ...

    def put_price_snapshot(self, snapshot: PriceSnapshot) -> None: ...

    def put_price_signal(self, signal: PriceSignal) -> None: ...

    def put_info_signal(self, signal: InfoSignal) -> None: ...

    def put_mode_state(self, mode_state: ModeState) -> None: ...

    def register_trade(self, mode: str, realized_pnl: float | None = None) -> None: ...

    def reset_trade_stats(self) -> TradeStats: ...

    def save_portfolio(self) -> None: ...

    def save_phase_gate(self, state: PhaseGateState) -> None: ...

    def record_order(self, order: OrderRecord) -> None: ...

    def record_fill(self, fill: FillRecord) -> None: ...

    def has_fill(self, order_id: str) -> bool: ...

    def update_order_status(self, order_id: str, status: str, raw: dict | None = None) -> bool: ...

    def record_trade_log(self, payload: dict) -> None: ...

    def record_positions_snapshot(self, snapshot: PortfolioState) -> None: ...

    def upsert_execution_compensation(self, key: str, intent: OrderIntent) -> None: ...

    def delete_execution_compensation(self, key: str) -> None: ...

    def replay_window(self, minutes: int) -> dict[str, object]: ...

    def metrics_summary(self) -> dict[str, float]: ...

    def get_current_params(self) -> ParamsVersion: ...

    def get_params_version(self, version_id: str) -> ParamsVersion | None: ...

    def save_params_version(
        self,
        params: StrategyParams,
        *,
        source: str = "system",
        score: float | None = None,
        make_current: bool = True,
    ) -> ParamsVersion: ...

    def set_current_params_version(self, version_id: str) -> ParamsVersion | None: ...

    def save_optimization_trial(self, trial: OptimizationTrial) -> None: ...

    def optimization_leaderboard(self, limit: int = 20) -> list[OptimizationTrial]: ...

    def save_optimization_meta(
        self,
        *,
        decline_streak: int,
        last_applied_score: float | None,
        last_applied_version_id: str,
    ) -> None: ...

    def update_price_stream_state(
        self,
        *,
        running: bool | None = None,
        last_event_ts: datetime | None = None,
        reconnects: int | None = None,
    ) -> None: ...

    def reset_runtime_state(
        self,
        *,
        clear_markets: bool = False,
        clear_trade_stats: bool = False,
        clear_portfolio_controls: bool = False,
    ) -> dict[str, int]: ...


class InMemoryRepository:
    def __init__(self) -> None:
        self.markets: dict[str, MarketCard] = {}
        self.price_snapshots: dict[str, PriceSnapshot] = {}
        self.price_signals: dict[str, PriceSignal] = {}
        self.info_signals: dict[str, InfoSignal] = {}
        self.mode_states: dict[str, ModeState] = {}
        self.trade_stats = TradeStats()
        self.portfolio = PortfolioState()
        self.phase_gate_state = PhaseGateState()
        self.order_records: list[OrderRecord] = []
        self.fill_records: list[FillRecord] = []
        self.trade_logs: list[dict] = []
        self.positions_snapshots: list[PortfolioState] = []
        self.execution_compensations: dict[str, OrderIntent] = {}
        self.strategy_params_history: list[ParamsVersion] = []
        self.strategy_params_current = ParamsVersion(source="bootstrap", params=StrategyParams())
        self.strategy_params_history.append(self.strategy_params_current.model_copy(deep=True))
        self.optimization_trials: list[OptimizationTrial] = []
        self.optimization_decline_streak: int = 0
        self.optimization_last_applied_score: float | None = None
        self.optimization_last_applied_version_id: str = self.strategy_params_current.version_id
        self.price_signal_events: list[PriceSignal] = []
        self.mode_state_events: list[ModeState] = []
        self.price_stream_running: bool = False
        self.price_stream_last_event_ts: datetime | None = None
        self.price_stream_reconnects: int = 0

    def upsert_market(self, market: MarketCard) -> None:
        self.markets[market.market_id] = market

    def replace_markets(self, markets: list[MarketCard]) -> None:
        self.markets = {market.market_id: market for market in markets}

    def put_price_snapshot(self, snapshot: PriceSnapshot) -> None:
        self.price_snapshots[snapshot.market_id] = snapshot

    def put_price_signal(self, signal: PriceSignal) -> None:
        self.price_signals[signal.market_id] = signal
        self.price_signal_events.append(signal)

    def put_info_signal(self, signal: InfoSignal) -> None:
        self.info_signals[signal.market_id] = signal

    def put_mode_state(self, mode_state: ModeState) -> None:
        self.mode_states[mode_state.market_id] = mode_state
        self.mode_state_events.append(mode_state)

    def register_trade(self, mode: str, realized_pnl: float | None = None) -> None:
        self.trade_stats.total_trades += 1
        if mode == "B":
            self.trade_stats.b_trades += 1
        if self.trade_stats.total_trades > 0:
            self.portfolio.b_trade_share = self.trade_stats.b_trades / self.trade_stats.total_trades
        if realized_pnl is not None:
            if realized_pnl > 0:
                self.trade_stats.wins += 1
                self.trade_stats.gross_profit += realized_pnl
            elif realized_pnl < 0:
                self.trade_stats.losses += 1
                self.trade_stats.gross_loss += abs(realized_pnl)
            if self.trade_stats.wins > 0:
                self.trade_stats.avg_win = self.trade_stats.gross_profit / self.trade_stats.wins
            if self.trade_stats.losses > 0:
                self.trade_stats.avg_loss = self.trade_stats.gross_loss / self.trade_stats.losses

    def reset_trade_stats(self) -> TradeStats:
        self.trade_stats = TradeStats()
        return self.trade_stats

    def save_portfolio(self) -> None:
        return None

    def save_phase_gate(self, state: PhaseGateState) -> None:
        self.phase_gate_state = state
        self.portfolio.phase = state.phase

    def record_order(self, order: OrderRecord) -> None:
        self.order_records.append(order)

    def record_fill(self, fill: FillRecord) -> None:
        self.fill_records.append(fill)

    def has_fill(self, order_id: str) -> bool:
        return any(fill.order_id == order_id for fill in self.fill_records)

    def update_order_status(self, order_id: str, status: str, raw: dict | None = None) -> bool:
        for idx, record in enumerate(self.order_records):
            if record.order_id != order_id:
                continue
            payload = record.model_dump(mode="json")
            payload["status"] = status
            if raw is not None:
                payload["raw"] = raw
            self.order_records[idx] = OrderRecord.model_validate(payload)
            return True
        return False

    def record_trade_log(self, payload: dict) -> None:
        self.trade_logs.append(payload)

    def record_positions_snapshot(self, snapshot: PortfolioState) -> None:
        self.positions_snapshots.append(snapshot.model_copy(deep=True))

    def upsert_execution_compensation(self, key: str, intent: OrderIntent) -> None:
        self.execution_compensations[key] = OrderIntent.model_validate(intent.model_dump(mode="json"))

    def delete_execution_compensation(self, key: str) -> None:
        self.execution_compensations.pop(key, None)

    def replay_window(self, minutes: int) -> dict[str, object]:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        orders = [o for o in self.order_records if o.ts >= cutoff]
        fills = [f for f in self.fill_records if f.ts >= cutoff]
        signal_events = [s for s in self.price_signal_events if s.ts >= cutoff]
        mode_events = [m for m in self.mode_state_events if m.ts >= cutoff]

        def _latest_signal_before(market_id: str, ts: datetime) -> PriceSignal | None:
            matches = [s for s in self.price_signal_events if s.market_id == market_id and s.ts <= ts]
            return matches[-1] if matches else None

        def _latest_mode_before(market_id: str, ts: datetime) -> ModeState | None:
            matches = [m for m in self.mode_state_events if m.market_id == market_id and m.ts <= ts]
            return matches[-1] if matches else None

        events: list[dict[str, object]] = []
        for s in signal_events:
            events.append(
                {
                    "ts": s.ts.isoformat(),
                    "event": "price_signal",
                    "market_id": s.market_id,
                    "payload": s.model_dump(mode="json"),
                }
            )
        for m in mode_events:
            events.append(
                {
                    "ts": m.ts.isoformat(),
                    "event": "mode_state",
                    "market_id": m.market_id,
                    "payload": m.model_dump(mode="json"),
                }
            )
        for f in fills:
            events.append(
                {
                    "ts": f.ts.isoformat(),
                    "event": "fill",
                    "market_id": f.market_id,
                    "payload": f.model_dump(mode="json"),
                }
            )
        for o in orders:
            signal = _latest_signal_before(o.market_id, o.ts)
            mode = _latest_mode_before(o.market_id, o.ts)
            events.append(
                {
                    "ts": o.ts.isoformat(),
                    "event": "order",
                    "market_id": o.market_id,
                    "payload": o.model_dump(mode="json"),
                    "strategy_input": {
                        "price_signal": signal.model_dump(mode="json") if signal else None,
                        "mode_state": mode.model_dump(mode="json") if mode else None,
                    },
                }
            )

        events.sort(key=lambda item: str(item.get("ts", "")))
        return {
            "minutes": minutes,
            "orders": len(orders),
            "fills": len(fills),
            "price_signals": len(signal_events),
            "mode_states": len(mode_events),
            "events": events,
        }

    def metrics_summary(self) -> dict[str, float]:
        realized_trades = self.trade_stats.wins + self.trade_stats.losses
        win_rate_denominator = realized_trades if realized_trades > 0 else self.trade_stats.total_trades
        if win_rate_denominator <= 0:
            win_rate = 0.0
            b_share = 0.0
        else:
            win_rate = self.trade_stats.wins / win_rate_denominator
            b_share = self.trade_stats.b_trades / self.trade_stats.total_trades if self.trade_stats.total_trades > 0 else 0.0
        rr = (
            self.trade_stats.gross_profit / self.trade_stats.gross_loss
            if self.trade_stats.gross_loss > 0
            else (float("inf") if self.trade_stats.gross_profit > 0 else 0.0)
        )
        return {
            "win_rate": win_rate,
            "rr": rr,
            "max_drawdown_pct": self.portfolio.max_drawdown_pct,
            "total_trades": float(self.trade_stats.total_trades),
            "b_trade_share": b_share,
        }

    def get_current_params(self) -> ParamsVersion:
        return self.strategy_params_current.model_copy(deep=True)

    def get_params_version(self, version_id: str) -> ParamsVersion | None:
        for item in self.strategy_params_history:
            if item.version_id == version_id:
                return item.model_copy(deep=True)
        return None

    def save_params_version(
        self,
        params: StrategyParams,
        *,
        source: str = "system",
        score: float | None = None,
        make_current: bool = True,
    ) -> ParamsVersion:
        item = ParamsVersion(source=source, score=score, params=params.model_copy(deep=True))
        self.strategy_params_history.append(item.model_copy(deep=True))
        if make_current:
            self.strategy_params_current = item.model_copy(deep=True)
            self.optimization_last_applied_version_id = item.version_id
        return item

    def set_current_params_version(self, version_id: str) -> ParamsVersion | None:
        item = self.get_params_version(version_id)
        if item is None:
            return None
        self.strategy_params_current = item.model_copy(deep=True)
        self.optimization_last_applied_version_id = item.version_id
        return self.strategy_params_current.model_copy(deep=True)

    def save_optimization_trial(self, trial: OptimizationTrial) -> None:
        self.optimization_trials.append(trial.model_copy(deep=True))

    def optimization_leaderboard(self, limit: int = 20) -> list[OptimizationTrial]:
        ordered = sorted(self.optimization_trials, key=lambda item: item.score, reverse=True)
        top = ordered[: max(limit, 0)]
        return [item.model_copy(deep=True) for item in top]

    def save_optimization_meta(
        self,
        *,
        decline_streak: int,
        last_applied_score: float | None,
        last_applied_version_id: str,
    ) -> None:
        self.optimization_decline_streak = max(decline_streak, 0)
        self.optimization_last_applied_score = last_applied_score
        self.optimization_last_applied_version_id = last_applied_version_id

    def update_price_stream_state(
        self,
        *,
        running: bool | None = None,
        last_event_ts: datetime | None = None,
        reconnects: int | None = None,
    ) -> None:
        if running is not None:
            self.price_stream_running = running
        if last_event_ts is not None:
            self.price_stream_last_event_ts = last_event_ts
        if reconnects is not None:
            self.price_stream_reconnects = reconnects

    def reset_runtime_state(
        self,
        *,
        clear_markets: bool = False,
        clear_trade_stats: bool = False,
        clear_portfolio_controls: bool = False,
    ) -> dict[str, int]:
        if clear_markets:
            self.markets.clear()
        self.price_snapshots.clear()
        self.price_signals.clear()
        self.info_signals.clear()
        self.mode_states.clear()
        self.order_records.clear()
        self.fill_records.clear()
        self.trade_logs.clear()
        self.positions_snapshots.clear()
        self.execution_compensations.clear()
        self.optimization_trials.clear()
        self.optimization_decline_streak = 0
        self.optimization_last_applied_score = None
        self.optimization_last_applied_version_id = self.strategy_params_current.version_id
        self.price_signal_events.clear()
        self.mode_state_events.clear()
        self.price_stream_running = False
        self.price_stream_last_event_ts = None
        self.price_stream_reconnects = 0

        if clear_trade_stats:
            self.reset_trade_stats()

        if clear_portfolio_controls:
            self.portfolio.daily_halt = False
            self.portfolio.pause_until = None
            self.portfolio.consecutive_losses = 0

        return {
            "markets": len(self.markets),
            "price_snapshots": len(self.price_snapshots),
            "price_signals": len(self.price_signals),
            "info_signals": len(self.info_signals),
            "mode_states": len(self.mode_states),
            "orders": len(self.order_records),
            "fills": len(self.fill_records),
            "positions_snapshots": len(self.positions_snapshots),
            "execution_compensations": len(self.execution_compensations),
            "optimization_trials": len(self.optimization_trials),
            "trade_stats_total": self.trade_stats.total_trades,
            "trade_stats_b": self.trade_stats.b_trades,
            "daily_halt": int(self.portfolio.daily_halt),
            "has_pause": int(self.portfolio.pause_until is not None),
            "consecutive_losses": self.portfolio.consecutive_losses,
        }


class SqliteRepository(InMemoryRepository):
    """Minimal persistent repository for T1 loops."""

    def __init__(self, db_path: str) -> None:
        super().__init__()
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()
        self._load_state()

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS kv_state (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS markets_cache (
              market_id TEXT PRIMARY KEY,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS price_snapshot_cache (
              market_id TEXT PRIMARY KEY,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS price_signal_cache (
              market_id TEXT PRIMARY KEY,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS info_signal_cache (
              market_id TEXT PRIMARY KEY,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mode_state_cache (
              market_id TEXT PRIMARY KEY,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
              ts TEXT NOT NULL,
              order_id TEXT PRIMARY KEY,
              market_id TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fills (
              ts TEXT NOT NULL,
              order_id TEXT NOT NULL,
              market_id TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trade_logs (
              ts TEXT NOT NULL,
              market_id TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS positions_snapshots (
              ts TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS execution_compensations (
              idempotency_key TEXT PRIMARY KEY,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS params_versions (
              version_id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS optimization_trials (
              trial_id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS price_signal_events (
              ts TEXT NOT NULL,
              market_id TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mode_state_events (
              ts TEXT NOT NULL,
              market_id TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_orders_ts ON orders(ts);
            CREATE INDEX IF NOT EXISTS idx_fills_ts ON fills(ts);
            CREATE INDEX IF NOT EXISTS idx_trade_logs_ts ON trade_logs(ts);
            CREATE INDEX IF NOT EXISTS idx_positions_snapshots_ts ON positions_snapshots(ts);
            CREATE INDEX IF NOT EXISTS idx_price_signal_events_ts ON price_signal_events(ts);
            CREATE INDEX IF NOT EXISTS idx_mode_state_events_ts ON mode_state_events(ts);
            CREATE INDEX IF NOT EXISTS idx_optimization_trials_created_at ON optimization_trials(created_at);
            """
        )
        self._conn.commit()

    def _load_state(self) -> None:
        for row in self._conn.execute("SELECT market_id,payload FROM markets_cache"):
            self.markets[row["market_id"]] = MarketCard.model_validate_json(row["payload"])
        for row in self._conn.execute("SELECT market_id,payload FROM price_snapshot_cache"):
            self.price_snapshots[row["market_id"]] = PriceSnapshot.model_validate_json(row["payload"])
        for row in self._conn.execute("SELECT market_id,payload FROM price_signal_cache"):
            self.price_signals[row["market_id"]] = PriceSignal.model_validate_json(row["payload"])
        for row in self._conn.execute("SELECT market_id,payload FROM info_signal_cache"):
            self.info_signals[row["market_id"]] = InfoSignal.model_validate_json(row["payload"])
        for row in self._conn.execute("SELECT market_id,payload FROM mode_state_cache"):
            self.mode_states[row["market_id"]] = ModeState.model_validate_json(row["payload"])

        row = self._conn.execute("SELECT value FROM kv_state WHERE key='trade_stats'").fetchone()
        if row:
            self.trade_stats = TradeStats.model_validate_json(row["value"])
        row = self._conn.execute("SELECT value FROM kv_state WHERE key='portfolio'").fetchone()
        if row:
            self.portfolio = PortfolioState.model_validate_json(row["value"])
        row = self._conn.execute("SELECT value FROM kv_state WHERE key='phase_gate_state'").fetchone()
        if row:
            self.phase_gate_state = PhaseGateState.model_validate_json(row["value"])
            self.portfolio.phase = self.phase_gate_state.phase
        row = self._conn.execute("SELECT value FROM kv_state WHERE key='strategy_params_current'").fetchone()
        if row:
            self.strategy_params_current = ParamsVersion.model_validate_json(row["value"])
            self.optimization_last_applied_version_id = self.strategy_params_current.version_id
        self.strategy_params_history = []
        for row in self._conn.execute("SELECT payload FROM params_versions ORDER BY created_at"):
            self.strategy_params_history.append(ParamsVersion.model_validate_json(row["payload"]))
        if not self.strategy_params_history:
            self.strategy_params_history = [self.strategy_params_current.model_copy(deep=True)]
            self._conn.execute(
                "INSERT OR REPLACE INTO params_versions (version_id, created_at, payload) VALUES (?,?,?)",
                (
                    self.strategy_params_current.version_id,
                    self.strategy_params_current.created_at.isoformat(),
                    self.strategy_params_current.model_dump_json(),
                ),
            )
            self._set_kv("strategy_params_current", self.strategy_params_current.model_dump_json())
        row = self._conn.execute("SELECT value FROM kv_state WHERE key='optimization_meta'").fetchone()
        if row:
            payload = json.loads(row["value"])
            self.optimization_decline_streak = int(payload.get("decline_streak", 0))
            self.optimization_last_applied_score = (
                float(payload["last_applied_score"]) if payload.get("last_applied_score") is not None else None
            )
            self.optimization_last_applied_version_id = str(
                payload.get("last_applied_version_id", self.optimization_last_applied_version_id)
            )
        row = self._conn.execute("SELECT value FROM kv_state WHERE key='price_stream_state'").fetchone()
        if row:
            payload = json.loads(row["value"])
            self.price_stream_running = bool(payload.get("running", False))
            self.price_stream_reconnects = int(payload.get("reconnects", 0))
            raw_ts = payload.get("last_event_ts")
            if raw_ts:
                try:
                    self.price_stream_last_event_ts = datetime.fromisoformat(str(raw_ts))
                except ValueError:
                    self.price_stream_last_event_ts = None

        for row in self._conn.execute("SELECT payload FROM orders ORDER BY ts"):
            self.order_records.append(OrderRecord.model_validate_json(row["payload"]))
        for row in self._conn.execute("SELECT payload FROM fills ORDER BY ts"):
            self.fill_records.append(FillRecord.model_validate_json(row["payload"]))
        for row in self._conn.execute("SELECT payload FROM trade_logs ORDER BY ts"):
            raw_payload = row["payload"]
            try:
                parsed = json.loads(raw_payload)
                self.trade_logs.append(parsed if isinstance(parsed, dict) else {"payload": parsed})
            except json.JSONDecodeError:
                self.trade_logs.append({"payload_raw": raw_payload})
        for row in self._conn.execute("SELECT payload FROM positions_snapshots ORDER BY ts"):
            self.positions_snapshots.append(PortfolioState.model_validate_json(row["payload"]))
        for row in self._conn.execute(
            "SELECT idempotency_key,payload FROM execution_compensations ORDER BY idempotency_key"
        ):
            self.execution_compensations[row["idempotency_key"]] = OrderIntent.model_validate_json(row["payload"])
        for row in self._conn.execute("SELECT payload FROM optimization_trials ORDER BY created_at"):
            self.optimization_trials.append(OptimizationTrial.model_validate_json(row["payload"]))
        for row in self._conn.execute("SELECT payload FROM price_signal_events ORDER BY ts"):
            self.price_signal_events.append(PriceSignal.model_validate_json(row["payload"]))
        for row in self._conn.execute("SELECT payload FROM mode_state_events ORDER BY ts"):
            self.mode_state_events.append(ModeState.model_validate_json(row["payload"]))

    def _upsert_cache(self, table: str, key: str, payload: str) -> None:
        self._conn.execute(
            f"INSERT INTO {table} (market_id,payload) VALUES (?,?) "
            "ON CONFLICT(market_id) DO UPDATE SET payload=excluded.payload",
            (key, payload),
        )
        self._conn.commit()

    def _set_kv(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO kv_state (key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    def upsert_market(self, market: MarketCard) -> None:
        super().upsert_market(market)
        self._upsert_cache("markets_cache", market.market_id, market.model_dump_json())

    def replace_markets(self, markets: list[MarketCard]) -> None:
        super().replace_markets(markets)
        self._conn.execute("DELETE FROM markets_cache")
        for market in markets:
            self._conn.execute(
                "INSERT INTO markets_cache (market_id,payload) VALUES (?,?)",
                (market.market_id, market.model_dump_json()),
            )
        self._conn.commit()

    def put_price_snapshot(self, snapshot: PriceSnapshot) -> None:
        super().put_price_snapshot(snapshot)
        self._upsert_cache("price_snapshot_cache", snapshot.market_id, snapshot.model_dump_json())

    def put_price_signal(self, signal: PriceSignal) -> None:
        super().put_price_signal(signal)
        self._upsert_cache("price_signal_cache", signal.market_id, signal.model_dump_json())
        self._conn.execute(
            "INSERT INTO price_signal_events (ts,market_id,payload) VALUES (?,?,?)",
            (signal.ts.isoformat(), signal.market_id, signal.model_dump_json()),
        )
        self._conn.commit()

    def put_info_signal(self, signal: InfoSignal) -> None:
        super().put_info_signal(signal)
        self._upsert_cache("info_signal_cache", signal.market_id, signal.model_dump_json())

    def put_mode_state(self, mode_state: ModeState) -> None:
        super().put_mode_state(mode_state)
        self._upsert_cache("mode_state_cache", mode_state.market_id, mode_state.model_dump_json())
        self._conn.execute(
            "INSERT INTO mode_state_events (ts,market_id,payload) VALUES (?,?,?)",
            (mode_state.ts.isoformat(), mode_state.market_id, mode_state.model_dump_json()),
        )
        self._conn.commit()

    def register_trade(self, mode: str, realized_pnl: float | None = None) -> None:
        super().register_trade(mode, realized_pnl=realized_pnl)
        self._set_kv("trade_stats", self.trade_stats.model_dump_json())

    def reset_trade_stats(self) -> TradeStats:
        self.trade_stats = TradeStats()
        self._set_kv("trade_stats", self.trade_stats.model_dump_json())
        return self.trade_stats

    def save_portfolio(self) -> None:
        self._set_kv("portfolio", self.portfolio.model_dump_json())

    def save_phase_gate(self, state: PhaseGateState) -> None:
        super().save_phase_gate(state)
        self._set_kv("phase_gate_state", self.phase_gate_state.model_dump_json())

    def update_price_stream_state(
        self,
        *,
        running: bool | None = None,
        last_event_ts: datetime | None = None,
        reconnects: int | None = None,
    ) -> None:
        super().update_price_stream_state(running=running, last_event_ts=last_event_ts, reconnects=reconnects)
        payload = {
            "running": self.price_stream_running,
            "last_event_ts": self.price_stream_last_event_ts.isoformat() if self.price_stream_last_event_ts else None,
            "reconnects": self.price_stream_reconnects,
        }
        self._set_kv("price_stream_state", json.dumps(payload))

    def get_current_params(self) -> ParamsVersion:
        return super().get_current_params()

    def get_params_version(self, version_id: str) -> ParamsVersion | None:
        return super().get_params_version(version_id)

    def save_params_version(
        self,
        params: StrategyParams,
        *,
        source: str = "system",
        score: float | None = None,
        make_current: bool = True,
    ) -> ParamsVersion:
        item = super().save_params_version(params, source=source, score=score, make_current=make_current)
        self._conn.execute(
            "INSERT OR REPLACE INTO params_versions (version_id, created_at, payload) VALUES (?,?,?)",
            (item.version_id, item.created_at.isoformat(), item.model_dump_json()),
        )
        if make_current:
            self._set_kv("strategy_params_current", item.model_dump_json())
        else:
            self._conn.commit()
        return item

    def set_current_params_version(self, version_id: str) -> ParamsVersion | None:
        item = super().set_current_params_version(version_id)
        if item is None:
            return None
        self._set_kv("strategy_params_current", item.model_dump_json())
        return item

    def save_optimization_trial(self, trial: OptimizationTrial) -> None:
        super().save_optimization_trial(trial)
        item = self.optimization_trials[-1]
        self._conn.execute(
            "INSERT OR REPLACE INTO optimization_trials (trial_id, created_at, payload) VALUES (?,?,?)",
            (item.trial_id, item.created_at.isoformat(), item.model_dump_json()),
        )
        self._conn.commit()

    def optimization_leaderboard(self, limit: int = 20) -> list[OptimizationTrial]:
        return super().optimization_leaderboard(limit=limit)

    def save_optimization_meta(
        self,
        *,
        decline_streak: int,
        last_applied_score: float | None,
        last_applied_version_id: str,
    ) -> None:
        super().save_optimization_meta(
            decline_streak=decline_streak,
            last_applied_score=last_applied_score,
            last_applied_version_id=last_applied_version_id,
        )
        payload = {
            "decline_streak": self.optimization_decline_streak,
            "last_applied_score": self.optimization_last_applied_score,
            "last_applied_version_id": self.optimization_last_applied_version_id,
        }
        self._set_kv("optimization_meta", json.dumps(payload))

    def record_order(self, order: OrderRecord) -> None:
        super().record_order(order)
        self._conn.execute(
            "INSERT INTO orders (ts,order_id,market_id,payload) VALUES (?,?,?,?)",
            (order.ts.isoformat(), order.order_id, order.market_id, order.model_dump_json()),
        )
        self._conn.commit()

    def record_fill(self, fill: FillRecord) -> None:
        super().record_fill(fill)
        self._conn.execute(
            "INSERT INTO fills (ts,order_id,market_id,payload) VALUES (?,?,?,?)",
            (fill.ts.isoformat(), fill.order_id, fill.market_id, fill.model_dump_json()),
        )
        self._conn.commit()

    def has_fill(self, order_id: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM fills WHERE order_id=? LIMIT 1", (order_id,)).fetchone()
        return row is not None

    def update_order_status(self, order_id: str, status: str, raw: dict | None = None) -> bool:
        for idx, record in enumerate(self.order_records):
            if record.order_id != order_id:
                continue
            payload = record.model_dump(mode="json")
            payload["status"] = status
            if raw is not None:
                payload["raw"] = raw
            updated = OrderRecord.model_validate(payload)
            self.order_records[idx] = updated
            self._conn.execute(
                "UPDATE orders SET payload=? WHERE order_id=?",
                (updated.model_dump_json(), order_id),
            )
            self._conn.commit()
            return True
        return False

    def record_trade_log(self, payload: dict) -> None:
        super().record_trade_log(payload)
        self._conn.execute(
            "INSERT INTO trade_logs (ts,market_id,payload) VALUES (?,?,?)",
            (
                str(payload.get("ts", "")),
                str(payload.get("market_id", "")),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
            ),
        )
        self._conn.commit()

    def record_positions_snapshot(self, snapshot: PortfolioState) -> None:
        super().record_positions_snapshot(snapshot)
        self._conn.execute(
            "INSERT INTO positions_snapshots (ts,payload) VALUES (?,?)",
            (snapshot.ts.isoformat(), snapshot.model_dump_json()),
        )
        self._conn.commit()

    def upsert_execution_compensation(self, key: str, intent: OrderIntent) -> None:
        super().upsert_execution_compensation(key, intent)
        persisted = self.execution_compensations[key]
        self._conn.execute(
            "INSERT INTO execution_compensations (idempotency_key,payload) VALUES (?,?) "
            "ON CONFLICT(idempotency_key) DO UPDATE SET payload=excluded.payload",
            (key, persisted.model_dump_json()),
        )
        self._conn.commit()

    def delete_execution_compensation(self, key: str) -> None:
        super().delete_execution_compensation(key)
        self._conn.execute("DELETE FROM execution_compensations WHERE idempotency_key=?", (key,))
        self._conn.commit()

    def reset_runtime_state(
        self,
        *,
        clear_markets: bool = False,
        clear_trade_stats: bool = False,
        clear_portfolio_controls: bool = False,
    ) -> dict[str, int]:
        payload = super().reset_runtime_state(
            clear_markets=clear_markets,
            clear_trade_stats=clear_trade_stats,
            clear_portfolio_controls=clear_portfolio_controls,
        )
        if clear_markets:
            self._conn.execute("DELETE FROM markets_cache")
        self._conn.execute("DELETE FROM price_snapshot_cache")
        self._conn.execute("DELETE FROM price_signal_cache")
        self._conn.execute("DELETE FROM info_signal_cache")
        self._conn.execute("DELETE FROM mode_state_cache")
        self._conn.execute("DELETE FROM orders")
        self._conn.execute("DELETE FROM fills")
        self._conn.execute("DELETE FROM trade_logs")
        self._conn.execute("DELETE FROM positions_snapshots")
        self._conn.execute("DELETE FROM execution_compensations")
        self._conn.execute("DELETE FROM optimization_trials")
        self._conn.execute("DELETE FROM price_signal_events")
        self._conn.execute("DELETE FROM mode_state_events")
        self._conn.commit()
        if clear_trade_stats:
            self._set_kv("trade_stats", self.trade_stats.model_dump_json())
        if clear_portfolio_controls:
            self.save_portfolio()
        self.save_optimization_meta(
            decline_streak=self.optimization_decline_streak,
            last_applied_score=self.optimization_last_applied_score,
            last_applied_version_id=self.optimization_last_applied_version_id,
        )
        return payload

    def close(self) -> None:
        self._conn.close()


def build_repository(settings: AppSettings) -> Repository:
    if settings.repository_backend == "sqlite":
        return SqliteRepository(settings.sqlite_path)
    return InMemoryRepository()
