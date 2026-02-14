from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass


@dataclass
class StructuralResult:
    signal_type: str
    side: str
    edge_bps: float
    reason: str


def detect_pair_cost_signal(yes_mid: float, no_mid: float, fee_pct: float) -> StructuralResult | None:
    pair_cost = yes_mid + no_mid
    gross_edge = 1.0 - pair_cost
    fee = 2 * fee_pct
    net_edge = gross_edge - fee
    edge_bps = net_edge * 10_000

    if edge_bps > 0:
        return StructuralResult(
            signal_type="pair_cost_arb",
            side="buy_basket",
            edge_bps=edge_bps,
            reason=f"pair_cost={pair_cost:.4f}, net_edge_bps={edge_bps:.1f}",
        )
    return None


def detect_cross_market_signal(
    market: dict,
    quote: dict,
    peer_markets: list[dict],
    gap_bps_threshold: float,
) -> StructuralResult | None:
    yes_mid = float(quote.get("yes_mid") or 0.0)
    if not peer_markets:
        return None
    peer_probs: list[float] = []
    for peer in peer_markets:
        prices = peer.get("outcome_prices") or []
        if len(prices) >= 2:
            try:
                peer_probs.append(float(prices[0]))
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
        prices = peer.get("outcome_prices") or []
        if not peer_end or len(prices) < 2:
            continue
        try:
            dt = datetime.fromisoformat(str(peer_end).replace("Z", "+00:00")).astimezone(timezone.utc)
            p = float(prices[0])
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
