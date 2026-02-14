from __future__ import annotations

from datetime import datetime, timedelta, timezone

from chamiclaw.utils.time import utc_now_iso


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)


def _nearest_future_quote(future_quotes: list[dict], target_ts: datetime) -> dict | None:
    if not future_quotes:
        return None
    best = None
    best_diff = None
    for q in future_quotes:
        ts = q.get("ts_utc")
        if not ts:
            continue
        try:
            qts = _parse_ts(ts)
        except ValueError:
            continue
        if qts < target_ts:
            continue
        diff = abs((qts - target_ts).total_seconds())
        if best is None or diff < (best_diff or 10**18):
            best = q
            best_diff = diff
    return best


def evaluate_signal_horizons(signal: dict, current_quote: dict, future_quotes: list[dict], horizons_min: list[int]) -> list[dict]:
    out: list[dict] = []
    entry_prob = float(current_quote["yes_mid"])
    entry_ts = _parse_ts(current_quote["ts_utc"])
    side = str(signal.get("side", "buy_yes"))

    for h in horizons_min:
        target = entry_ts + timedelta(minutes=int(h))
        hit = _nearest_future_quote(future_quotes, target)
        exit_prob = float(hit["yes_mid"]) if hit else entry_prob
        signed = (exit_prob - entry_prob) if side in {"buy_yes", "buy_basket"} else (entry_prob - exit_prob)
        realized = signed * 10_000
        out.append(
            {
                "signal_id": signal["signal_id"],
                "market_id": signal["market_id"],
                "horizon_min": h,
                "entry_prob": entry_prob,
                "exit_prob": exit_prob,
                "realized_edge_bps": realized,
                "created_at_utc": utc_now_iso(),
            }
        )
    return out
