from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any


@dataclass
class StructuralResult:
    signal_type: str
    side: str
    edge_bps: float
    reason: str


def normalize_price(value: Any) -> tuple[float, str]:
    try:
        p = float(value)
    except (TypeError, ValueError):
        return 0.0, "invalid"
    if p > 100:
        return p / 1_000_000.0, "1e6"
    if p > 1:
        return p / 100.0, "pct"
    return p, "none"


def calc_pair_cost_edge_bps(yes_bid_raw: Any, yes_ask_raw: Any, no_bid_raw: Any, no_ask_raw: Any) -> dict[str, Any]:
    yes_bid, s1 = normalize_price(yes_bid_raw)
    yes_ask, s2 = normalize_price(yes_ask_raw)
    no_bid, s3 = normalize_price(no_bid_raw)
    no_ask, s4 = normalize_price(no_ask_raw)
    yes_mid = (yes_bid + yes_ask) / 2.0
    no_mid = (no_bid + no_ask) / 2.0
    pair_sum = yes_mid + no_mid
    raw_edge_bps = abs(pair_sum - 1.0) * 10_000
    scale = "none"
    for s in (s1, s2, s3, s4):
        if s == "1e6":
            scale = "1e6"
            break
        if s == "pct":
            scale = "pct"
    pair_sum_valid = 0.8 <= pair_sum <= 1.2
    return {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "yes_mid": yes_mid,
        "no_mid": no_mid,
        "pair_sum": pair_sum,
        "raw_edge_bps": raw_edge_bps,
        "pair_sum_valid": pair_sum_valid,
        "normalization_scale": scale,
    }


def detect_pair_cost_signal(
    yes_bid: float,
    yes_ask: float,
    no_bid: float,
    no_ask: float,
    enter_edge_bps: float,
) -> StructuralResult | None:
    calc = calc_pair_cost_edge_bps(yes_bid, yes_ask, no_bid, no_ask)
    pair_cost_edge_bps = float(calc["raw_edge_bps"])
    if not bool(calc.get("pair_sum_valid", False)):
        return None
    if pair_cost_edge_bps >= enter_edge_bps:
        side = "buy_basket" if float(calc["pair_sum"]) < 1.0 else "buy_no"
        return StructuralResult(
            signal_type="pair_cost_arb",
            side=side,
            edge_bps=pair_cost_edge_bps,
            reason=f"pair_cost_edge_bps={pair_cost_edge_bps:.1f}",
        )
    return None


def detect_cross_market_signal(
    market: dict,
    quote: dict,
    peer_quotes: list[dict],
    gap_bps_threshold: float,
) -> StructuralResult | None:
    yes_mid = float(quote.get("yes_mid") or 0.0)
    if not peer_quotes:
        return None
    peer_probs: list[float] = []
    for q in peer_quotes:
        yb = q.get("yes_bid")
        ya = q.get("yes_ask")
        if yb is None or ya is None:
            continue
        try:
            peer_probs.append((float(yb) + float(ya)) / 2.0)
        except (TypeError, ValueError):
            continue
    if not peer_probs:
        return None
    peer_avg = sum(peer_probs) / len(peer_probs)
    gap_bps = (yes_mid - peer_avg) * 10_000
    if abs(gap_bps) < gap_bps_threshold:
        return None
    side = "buy_no" if gap_bps > 0 else "buy_yes"
    return StructuralResult(
        signal_type="cross_market_divergence",
        side=side,
        edge_bps=abs(gap_bps),
        reason=f"market_prob={yes_mid:.4f}, peer_avg={peer_avg:.4f}, gap_bps={gap_bps:.1f}",
    )


def detect_term_structure_signal(
    market: dict,
    quote: dict,
    peer_markets: list[dict],
    peer_quotes_by_market_id: dict[str, dict],
    inversion_bps_threshold: float,
) -> StructuralResult | None:
    end_time = market.get("end_time_utc")
    if not end_time:
        return None
    try:
        this_end = datetime.fromisoformat(str(end_time).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
    yes_mid = float(quote.get("yes_mid") or 0.0)

    earlier_probs: list[float] = []
    later_probs: list[float] = []
    for peer in peer_markets:
        peer_end = peer.get("end_time_utc")
        if not peer_end:
            continue
        pq = peer_quotes_by_market_id.get(str(peer.get("market_id")))
        if not pq:
            continue
        yb = pq.get("yes_bid")
        ya = pq.get("yes_ask")
        if yb is None or ya is None:
            continue
        try:
            dt = datetime.fromisoformat(str(peer_end).replace("Z", "+00:00")).astimezone(timezone.utc)
            p = (float(yb) + float(ya)) / 2.0
        except (ValueError, TypeError):
            continue
        if dt < this_end:
            earlier_probs.append(p)
        elif dt > this_end:
            later_probs.append(p)

    if not earlier_probs and not later_probs:
        return None

    ref = (sum(earlier_probs) / len(earlier_probs)) if earlier_probs else (sum(later_probs) / len(later_probs))
    inversion_bps = (yes_mid - ref) * 10_000
    if abs(inversion_bps) < inversion_bps_threshold:
        return None
    side = "buy_no" if inversion_bps > 0 else "buy_yes"
    return StructuralResult(
        signal_type="term_structure_inversion",
        side=side,
        edge_bps=abs(inversion_bps),
        reason=f"market_prob={yes_mid:.4f}, term_ref={ref:.4f}, inversion_bps={inversion_bps:.1f}",
    )
