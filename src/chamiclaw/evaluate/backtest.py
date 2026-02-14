from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class BacktestMetrics:
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_like: float
    win_rate: float
    trades: int
    profit_factor: float
    avg_trade_return_pct: float


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)


def run_simple_backtest(
    quotes: list[dict],
    signals: list[dict],
    queue_delay_quotes: int = 1,
    slippage_bps: float = 10.0,
) -> BacktestMetrics:
    if not quotes or not signals:
        return BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0)

    by_market: dict[str, list[dict]] = {}
    for q in quotes:
        by_market.setdefault(q["market_id"], []).append(q)
    for m in by_market:
        by_market[m].sort(key=lambda x: _parse_ts(x["ts_utc"]))

    pnl_series: list[float] = []
    wins = 0
    gross_profit = 0.0
    gross_loss = 0.0
    for s in signals:
        market_id = s["market_id"]
        m_quotes = by_market.get(market_id, [])
        if len(m_quotes) < 2:
            continue
        created = s.get("created_at_utc")
        if not created:
            continue
        created_ts = _parse_ts(str(created))
        entry_idx = 0
        for i, q in enumerate(m_quotes):
            if _parse_ts(q["ts_utc"]) >= created_ts:
                entry_idx = min(len(m_quotes) - 1, i + max(0, queue_delay_quotes))
                break
        exit_idx = min(len(m_quotes) - 1, entry_idx + max(1, queue_delay_quotes))
        entry = float(m_quotes[entry_idx].get("yes_mid", 0.5))
        exit_ = float(m_quotes[exit_idx].get("yes_mid", entry))
        raw = exit_ - entry if s.get("side") in ("buy_yes", "buy_basket") else entry - exit_
        pnl = raw - (slippage_bps / 10_000.0)
        pnl_series.append(pnl)
        if pnl > 0:
            wins += 1
            gross_profit += pnl
        elif pnl < 0:
            gross_loss += abs(pnl)

    if not pnl_series:
        return BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0)

    equity = 1.0
    curve = [equity]
    for p in pnl_series:
        equity *= 1 + p
        curve.append(equity)

    peak = curve[0]
    max_dd = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak
            max_dd = max(max_dd, dd)

    mean = sum(pnl_series) / len(pnl_series)
    var = sum((x - mean) ** 2 for x in pnl_series) / len(pnl_series)
    sharpe_like = 0.0 if var == 0 else (mean / math.sqrt(var)) * math.sqrt(len(pnl_series))

    return BacktestMetrics(
        total_return_pct=(equity - 1) * 100,
        max_drawdown_pct=max_dd * 100,
        sharpe_like=sharpe_like,
        win_rate=wins / len(pnl_series),
        trades=len(pnl_series),
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0),
        avg_trade_return_pct=(sum(pnl_series) / len(pnl_series)) * 100,
    )
