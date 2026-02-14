from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class RiskDecision:
    approved: bool
    reject_code: str | None
    message: str
    details: dict | None = None


class RiskEngine:
    def __init__(self, config: dict) -> None:
        self.config = config

    def check(self, order_intent: dict) -> RiskDecision:
        risk = self.config["risk"]

        edge_after_costs = float(order_intent.get("expected_edge_after_costs_bps", 0.0))
        if edge_after_costs <= 0:
            return RiskDecision(
                False,
                "NEGATIVE_EDGE",
                "expected edge after costs must be positive",
                details={"actual": edge_after_costs, "threshold": 0.0},
            )

        if order_intent.get("spread_bps", 0) > risk["max_spread_bps"]:
            return RiskDecision(
                False,
                "SPREAD_TOO_WIDE",
                "spread exceeds threshold",
                details={"actual": float(order_intent.get("spread_bps", 0)), "threshold": float(risk["max_spread_bps"])},
            )

        if order_intent.get("position_pct", 0) > risk["per_market_pos_pct"]:
            return RiskDecision(
                False,
                "POS_LIMIT",
                "per-market position limit exceeded",
                details={"actual": float(order_intent.get("position_pct", 0)), "threshold": float(risk["per_market_pos_pct"])},
            )

        if order_intent.get("cluster_exposure_pct", 0) > risk["event_cluster_exposure_pct"]:
            return RiskDecision(
                False,
                "CLUSTER_LIMIT",
                "event cluster exposure exceeded",
                details={"actual": float(order_intent.get("cluster_exposure_pct", 0)), "threshold": float(risk["event_cluster_exposure_pct"])},
            )

        end_time = order_intent.get("end_time_utc")
        if end_time:
            try:
                dt = datetime.fromisoformat(str(end_time).replace("Z", "+00:00")).astimezone(timezone.utc)
                mins_left = (dt - datetime.now(timezone.utc)).total_seconds() / 60
                if mins_left < risk["pre_expiry_add_position_block_min"] and order_intent.get("is_add_position", True):
                    return RiskDecision(
                        False,
                        "PRE_EXPIRY_BLOCK",
                        "cannot add close to expiry",
                        details={"actual": mins_left, "threshold": float(risk["pre_expiry_add_position_block_min"])},
                    )
            except ValueError:
                return RiskDecision(False, "BAD_END_TIME", "invalid end time", details={"actual": str(end_time)})

        if order_intent.get("daily_drawdown_pct", 0) >= risk["daily_max_drawdown_pct"]:
            return RiskDecision(
                False,
                "DAILY_DD",
                "daily max drawdown reached",
                details={"actual": float(order_intent.get("daily_drawdown_pct", 0)), "threshold": float(risk["daily_max_drawdown_pct"])},
            )

        if order_intent.get("open_orders_same_market", 0) >= risk.get("max_open_orders_per_market", 1):
            return RiskDecision(
                False,
                "OPEN_ORDER_LIMIT",
                "too many open orders",
                details={"actual": int(order_intent.get("open_orders_same_market", 0)), "threshold": int(risk.get("max_open_orders_per_market", 1))},
            )

        return RiskDecision(True, None, "ok", details={"expected_edge_after_costs_bps": edge_after_costs})
