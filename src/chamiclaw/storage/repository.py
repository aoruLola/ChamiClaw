from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

from chamiclaw.core.models import (
    InfoSignal,
    MarketCard,
    ModeState,
    PortfolioState,
    PriceSignal,
    PriceSnapshot,
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

    def upsert_market(self, market: MarketCard) -> None: ...

    def put_price_snapshot(self, snapshot: PriceSnapshot) -> None: ...

    def put_price_signal(self, signal: PriceSignal) -> None: ...

    def put_info_signal(self, signal: InfoSignal) -> None: ...

    def put_mode_state(self, mode_state: ModeState) -> None: ...

    def register_trade(self, mode: str) -> None: ...

    def reset_trade_stats(self) -> TradeStats: ...

    def save_portfolio(self) -> None: ...

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

    def upsert_market(self, market: MarketCard) -> None:
        self.markets[market.market_id] = market

    def put_price_snapshot(self, snapshot: PriceSnapshot) -> None:
        self.price_snapshots[snapshot.market_id] = snapshot

    def put_price_signal(self, signal: PriceSignal) -> None:
        self.price_signals[signal.market_id] = signal

    def put_info_signal(self, signal: InfoSignal) -> None:
        self.info_signals[signal.market_id] = signal

    def put_mode_state(self, mode_state: ModeState) -> None:
        self.mode_states[mode_state.market_id] = mode_state

    def register_trade(self, mode: str) -> None:
        self.trade_stats.total_trades += 1
        if mode == "B":
            self.trade_stats.b_trades += 1

    def reset_trade_stats(self) -> TradeStats:
        self.trade_stats = TradeStats()
        return self.trade_stats

    def save_portfolio(self) -> None:
        return None

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
        self._conn = sqlite3.connect(db_path)
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

    def put_price_snapshot(self, snapshot: PriceSnapshot) -> None:
        super().put_price_snapshot(snapshot)
        self._upsert_cache("price_snapshot_cache", snapshot.market_id, snapshot.model_dump_json())

    def put_price_signal(self, signal: PriceSignal) -> None:
        super().put_price_signal(signal)
        self._upsert_cache("price_signal_cache", signal.market_id, signal.model_dump_json())

    def put_info_signal(self, signal: InfoSignal) -> None:
        super().put_info_signal(signal)
        self._upsert_cache("info_signal_cache", signal.market_id, signal.model_dump_json())

    def put_mode_state(self, mode_state: ModeState) -> None:
        super().put_mode_state(mode_state)
        self._upsert_cache("mode_state_cache", mode_state.market_id, mode_state.model_dump_json())

    def register_trade(self, mode: str) -> None:
        super().register_trade(mode)
        self._set_kv("trade_stats", self.trade_stats.model_dump_json())

    def reset_trade_stats(self) -> TradeStats:
        self.trade_stats = TradeStats()
        self._set_kv("trade_stats", self.trade_stats.model_dump_json())
        return self.trade_stats

    def save_portfolio(self) -> None:
        self._set_kv("portfolio", self.portfolio.model_dump_json())

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
        self._conn.commit()
        if clear_trade_stats:
            self._set_kv("trade_stats", self.trade_stats.model_dump_json())
        if clear_portfolio_controls:
            self.save_portfolio()
        return payload

    def close(self) -> None:
        self._conn.close()


def build_repository(settings: AppSettings) -> Repository:
    if settings.repository_backend == "sqlite":
        return SqliteRepository(settings.sqlite_path)
    return InMemoryRepository()
