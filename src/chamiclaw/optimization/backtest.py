from __future__ import annotations

from datetime import datetime, timezone

from chamiclaw.core.models import BacktestReport, BacktestRequest
from chamiclaw.storage.repository import Repository


class BacktestEngine:
    def run(self, repo: Repository, request: BacktestRequest) -> BacktestReport:
        params_version_id = request.params_version_id or repo.get_current_params().version_id
        closed_logs = self._closed_trade_logs(repo, request.from_ts, request.to_ts)
        wins = sum(1 for row in closed_logs if row["pnl"] > 0)
        losses = sum(1 for row in closed_logs if row["pnl"] < 0)
        total_trades = wins + losses
        gross_profit = sum(row["pnl"] for row in closed_logs if row["pnl"] > 0)
        gross_loss = abs(sum(row["pnl"] for row in closed_logs if row["pnl"] < 0))
        win_rate = (wins / total_trades) if total_trades > 0 else 0.0
        rr = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
        mode_a_trades = sum(1 for row in closed_logs if row["mode"] == "A")
        mode_b_trades = sum(1 for row in closed_logs if row["mode"] == "B")
        max_drawdown_pct = self._max_drawdown_pct(closed_logs)
        score = self._score(win_rate=win_rate, rr=rr, max_drawdown_pct=max_drawdown_pct)
        return BacktestReport(
            from_ts=request.from_ts,
            to_ts=request.to_ts,
            params_version_id=params_version_id,
            total_trades=total_trades,
            win_rate=win_rate,
            rr=rr,
            max_drawdown_pct=max_drawdown_pct,
            mode_a_trades=mode_a_trades,
            mode_b_trades=mode_b_trades,
            score=score,
            sampled_price_signals=self._count_by_window(repo.price_signal_events, request.from_ts, request.to_ts),
            sampled_mode_states=self._count_by_window(repo.mode_state_events, request.from_ts, request.to_ts),
            sampled_orders=self._count_by_window(repo.order_records, request.from_ts, request.to_ts),
            sampled_fills=self._count_by_window(repo.fill_records, request.from_ts, request.to_ts),
        )

    @staticmethod
    def _score(*, win_rate: float, rr: float, max_drawdown_pct: float) -> float:
        rr_component = min(rr, 5.0) / 5.0 if rr != float("inf") else 1.0
        dd_penalty = min(max_drawdown_pct, 1.0)
        return 0.4 * win_rate + 0.3 * rr_component - 0.3 * dd_penalty

    @staticmethod
    def _count_by_window(rows: list, from_ts: datetime, to_ts: datetime) -> int:
        count = 0
        for row in rows:
            ts = getattr(row, "ts", None)
            if isinstance(ts, datetime) and from_ts <= ts <= to_ts:
                count += 1
        return count

    @staticmethod
    def _closed_trade_logs(repo: Repository, from_ts: datetime, to_ts: datetime) -> list[dict[str, float | str]]:
        rows: list[dict[str, float | str]] = []
        for item in repo.trade_logs:
            raw_ts = item.get("ts")
            if not isinstance(raw_ts, str):
                continue
            try:
                ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if not (from_ts <= ts <= to_ts):
                continue
            if str(item.get("action", "")).upper() != "CLOSE":
                continue
            try:
                pnl = float(item.get("pnl", 0.0))
            except (TypeError, ValueError):
                pnl = 0.0
            mode = str(item.get("mode", "")).upper()
            rows.append({"pnl": pnl, "mode": mode})
        return rows

    @staticmethod
    def _max_drawdown_pct(rows: list[dict[str, float | str]]) -> float:
        # Use a positive baseline so loss-only sequences still produce drawdown.
        equity = 1.0
        peak = 1.0
        max_drawdown = 0.0
        for row in rows:
            pnl = float(row["pnl"])
            equity += pnl
            peak = max(peak, equity)
            if peak <= 0:
                continue
            dd = (peak - equity) / peak
            max_drawdown = max(max_drawdown, dd)
        return max_drawdown
