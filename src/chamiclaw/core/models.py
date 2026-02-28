from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SpreadStatus(str, Enum):
    stable = "stable"
    wide = "wide"


class Mode(str, Enum):
    MODE_A = "MODE_A"
    MODE_B_ALLOWED = "MODE_B_ALLOWED"
    NO_TRADE = "NO_TRADE"


class Phase(str, Enum):
    PHASE_1 = "PHASE_1"
    PHASE_2 = "PHASE_2"


class OrderMode(str, Enum):
    A = "A"
    B = "B"


class Side(str, Enum):
    YES = "YES"
    NO = "NO"


class Action(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class MarketCard(BaseModel):
    market_id: str
    question: str
    outcomes: list[str] = Field(default_factory=lambda: ["YES", "NO"])
    end_time: datetime
    status: str
    tags: list[str] = Field(default_factory=list)
    rule_text: str = ""
    rule_summary: str = ""
    resolution_sources: list[str] = Field(default_factory=list)
    rule_clarity_score: float = 0.0
    liquidity_score: float = 0.0
    spread_stability: float = 0.0
    volume_density: float = 0.0
    event_risk_adjustment: float = 0.0
    market_score: float = 0.0


class PriceSnapshot(BaseModel):
    ts: datetime = Field(default_factory=utc_now)
    market_id: str
    best_bid: float
    best_ask: float
    mid: float
    spread: float
    last: float
    depth_topk: list[dict[str, float]] = Field(default_factory=list)
    trades_1m: int = 0
    volume_1m: float = 0.0


class NormalizedMarketTick(BaseModel):
    ts: datetime = Field(default_factory=utc_now)
    market_id: str
    best_bid: float
    best_ask: float
    last: float
    volume_1m: float = 0.0
    trades_1m: int = 0


class PriceSignal(BaseModel):
    ts: datetime = Field(default_factory=utc_now)
    market_id: str
    change_1m: float = 0.0
    change_5m: float = 0.0
    change_15m: float = 0.0
    vol_ratio_15m: float = 0.0
    spread_status: SpreadStatus = SpreadStatus.stable
    breakout_15m: bool = False
    anomaly_flag: bool = False
    spread: float = 0.0
    mid: float = 0.5


class InfoSignal(BaseModel):
    ts: datetime = Field(default_factory=utc_now)
    market_id: str
    event_detected: bool = False
    risk_score: float = 0.0
    confirmation_level: int = 0
    clarification_flag: bool = False
    top_sources: list[dict[str, str | int]] = Field(default_factory=list)
    extracted_claims: list[dict[str, str]] = Field(default_factory=list)


class ModeState(BaseModel):
    ts: datetime = Field(default_factory=utc_now)
    market_id: str
    mode: Mode
    reason_codes: list[str] = Field(default_factory=list)


class TradeStats(BaseModel):
    total_trades: int = 0
    b_trades: int = 0
    wins: int = 0
    losses: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0


class OrderIntent(BaseModel):
    ts: datetime = Field(default_factory=utc_now)
    market_id: str
    side: Side
    action: Action
    order_type: OrderType
    limit_price: float
    size_usd: float
    mode: OrderMode
    thesis: str
    ttl_seconds: int = 60
    idempotency_key: str = ""


class Position(BaseModel):
    market_id: str
    side: Side
    size: float
    avg_price: float
    u_pnl: float = 0.0


class PortfolioState(BaseModel):
    ts: datetime = Field(default_factory=utc_now)
    equity: float = 10_000.0
    cash: float = 10_000.0
    positions: list[Position] = Field(default_factory=list)
    daily_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    consecutive_losses: int = 0
    max_drawdown_pct: float = 0.0
    per_market_drawdown_pct: dict[str, float] = Field(default_factory=dict)
    pause_until: datetime | None = None
    daily_halt: bool = False
    phase: Phase = Phase.PHASE_1
    b_trade_share: float = 0.0


class ApprovedOrder(BaseModel):
    approved: bool
    reason: str = ""
    intent: OrderIntent | None = None


class ExecutionResult(BaseModel):
    accepted: bool
    order_id: str | None = None
    status: str = ""
    dry_run: bool = True
    raw: dict = Field(default_factory=dict)


class CancelResult(BaseModel):
    order_id: str
    cancelled: bool
    status: str = ""
    raw: dict = Field(default_factory=dict)


class OrderStatus(BaseModel):
    order_id: str
    status: str
    raw: dict = Field(default_factory=dict)


class PositionSnapshot(BaseModel):
    market_id: str
    side: Side
    size: float
    avg_price: float
    u_pnl: float = 0.0


class BalanceSnapshot(BaseModel):
    cash: float
    equity: float


class OrderRecord(BaseModel):
    ts: datetime = Field(default_factory=utc_now)
    order_id: str
    market_id: str
    side: Side
    action: Action
    order_type: OrderType
    limit_price: float | None = None
    size_usd: float
    status: str
    adapter: str
    mode: OrderMode
    dry_run: bool = True
    idempotency_key: str = ""
    raw: dict = Field(default_factory=dict)


class FillRecord(BaseModel):
    ts: datetime = Field(default_factory=utc_now)
    order_id: str
    market_id: str
    fill_price: float
    fill_size: float
    fee: float = 0.0
    raw: dict = Field(default_factory=dict)


class PnLAttribution(BaseModel):
    theoretical_pnl: float = 0.0
    actual_pnl: float = 0.0
    slippage: float = 0.0
    spread_at_entry: float = 0.0
    spread_at_exit: float = 0.0
    fee_ratio: float = 0.0


class PhaseGateState(BaseModel):
    ts: datetime = Field(default_factory=utc_now)
    phase: Phase = Phase.PHASE_1
    allowed_mode_b: bool = False
    reasons: list[str] = Field(default_factory=list)
