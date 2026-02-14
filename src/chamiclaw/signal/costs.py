from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CostBreakdown:
    fee_bps: float
    slippage_bps: float
    chain_bps: float

    @property
    def total_bps(self) -> float:
        return self.fee_bps + self.slippage_bps + self.chain_bps


def estimate_cost_bps(config: dict[str, Any], quote: dict[str, Any]) -> CostBreakdown:
    signal_cfg = config.get("signal", {})
    fee_bps = float(signal_cfg.get("trading_fee_pct", 0.0)) * 10_000

    base_slippage_bps = float(signal_cfg.get("slippage_bps", 0.0))
    assumed_notional_usd = float(signal_cfg.get("assumed_order_notional_usd", 100.0))
    depth_usd = max(1.0, float(quote.get("depth_usd") or assumed_notional_usd))
    depth_multiplier = max(1.0, assumed_notional_usd / depth_usd)
    slippage_bps = base_slippage_bps * depth_multiplier

    chain_bps = float(signal_cfg.get("chain_cost_bps", 0.0))
    return CostBreakdown(fee_bps=fee_bps, slippage_bps=slippage_bps, chain_bps=chain_bps)
