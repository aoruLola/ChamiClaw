from __future__ import annotations

from typing import Any


def emit_signal_decision(logger, **kwargs: Any) -> None:
    logger.log(
        "SIGNAL_ORDER_DECISION",
        signal_id=kwargs.get("signal_id"),
        market_id=kwargs.get("market_id"),
        raw_edge_bps=kwargs.get("raw_edge_bps"),
        net_edge_bps=kwargs.get("net_edge_bps"),
        spread_bps=kwargs.get("spread_bps"),
        fee_bps=kwargs.get("fee_bps"),
        slippage_bps=kwargs.get("slippage_bps"),
        confidence=kwargs.get("confidence"),
        gate_decision=kwargs.get("gate_decision"),
        reject_reason=kwargs.get("reject_reason"),
        risk_block_reason=kwargs.get("risk_block_reason"),
        execution_attempted=bool(kwargs.get("execution_attempted", False)),
    )


def emit_reject_summary(logger, reject_counts: dict[str, int]) -> None:
    logger.log("REJECT_SUMMARY", reject_counts=reject_counts)
