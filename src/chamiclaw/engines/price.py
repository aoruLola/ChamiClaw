from __future__ import annotations

from collections import defaultdict, deque

from chamiclaw.core.models import PriceSignal, PriceSnapshot, SpreadStatus


class PriceEngine:
    """Maintains a small rolling tape and produces snapshots/signals."""

    def __init__(self, max_points: int = 30):
        self._mid_tape: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=max_points))
        self._vol_tape: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=max_points))

    def on_quote(
        self,
        market_id: str,
        best_bid: float,
        best_ask: float,
        last: float,
        volume_1m: float,
        trades_1m: int,
    ) -> tuple[PriceSnapshot, PriceSignal]:
        mid = (best_bid + best_ask) / 2
        spread = max(best_ask - best_bid, 0.0)

        mids = self._mid_tape[market_id]
        vols = self._vol_tape[market_id]
        prev_mid = mids[-1] if mids else mid
        mids.append(mid)
        vols.append(volume_1m)

        change_1m = 0.0 if prev_mid == 0 else (mid - prev_mid) / prev_mid
        change_5m = self._change_over(mids, 5)
        change_15m = self._change_over(mids, 15)

        vol_short = sum(list(vols)[-3:]) / max(len(list(vols)[-3:]), 1)
        vol_long = sum(vols) / max(len(vols), 1)
        vol_ratio = 0.0 if vol_long == 0 else vol_short / vol_long

        spread_status = SpreadStatus.stable if spread <= 0.015 else SpreadStatus.wide
        breakout_15m = abs(change_15m) >= 0.03
        anomaly_flag = abs(change_1m) >= 0.02 or spread_status == SpreadStatus.wide

        snapshot = PriceSnapshot(
            market_id=market_id,
            best_bid=best_bid,
            best_ask=best_ask,
            mid=mid,
            spread=spread,
            last=last,
            depth_topk=[{"price": best_bid, "size": 0.0}, {"price": best_ask, "size": 0.0}],
            trades_1m=trades_1m,
            volume_1m=volume_1m,
        )
        signal = PriceSignal(
            market_id=market_id,
            change_1m=change_1m,
            change_5m=change_5m,
            change_15m=change_15m,
            vol_ratio_15m=vol_ratio,
            spread_status=spread_status,
            breakout_15m=breakout_15m,
            anomaly_flag=anomaly_flag,
            spread=spread,
            mid=mid,
        )
        return snapshot, signal

    @staticmethod
    def _change_over(values: deque[float], lookback: int) -> float:
        seq = list(values)
        if len(seq) < lookback:
            return 0.0
        old = seq[-lookback]
        new = seq[-1]
        return 0.0 if old == 0 else (new - old) / old
