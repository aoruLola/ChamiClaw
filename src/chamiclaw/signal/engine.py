from __future__ import annotations

import hashlib
from typing import Any

from chamiclaw.llm.interfaces import Llm1Generator, Llm2Validator, LlmProviderError
from chamiclaw.signal.costs import estimate_cost_bps
from chamiclaw.signal.structural import (
    detect_cross_market_signal,
    detect_pair_cost_signal,
    detect_term_structure_signal,
)
from chamiclaw.utils.time import utc_now_iso


class SignalEngine:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.llm1 = Llm1Generator(config)
        self.llm2 = Llm2Validator(config)

    def _signal_id(self, market_id: str, strategy_version: str, bucket: str) -> str:
        raw = f"{market_id}|{strategy_version}|{bucket}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:24]

    def generate(
        self,
        market: dict[str, Any],
        quote: dict[str, Any],
        strategy_version: str = "v0",
        peer_markets: list[dict[str, Any]] | None = None,
        debug: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        yes_mid = float(quote["yes_mid"])
        no_mid = float(quote["no_mid"])
        market_prob = yes_mid

        peers = peer_markets or []
        structural_candidates = []
        pair = detect_pair_cost_signal(yes_mid, no_mid, self.config["signal"]["trading_fee_pct"])
        if pair is not None:
            structural_candidates.append(pair)
        if self.config["signal"].get("enable_cross_market_signal", True):
            cross = detect_cross_market_signal(
                market=market,
                quote=quote,
                peer_markets=peers,
                gap_bps_threshold=float(self.config["signal"].get("cross_market_gap_bps", 150)),
            )
            if cross is not None:
                structural_candidates.append(cross)
        if self.config["signal"].get("enable_term_structure_signal", True):
            term = detect_term_structure_signal(
                market=market,
                quote=quote,
                peer_markets=peers,
                inversion_bps_threshold=float(self.config["signal"].get("term_structure_gap_bps", 120)),
            )
            if term is not None:
                structural_candidates.append(term)

        structural = None
        if structural_candidates:
            structural_candidates.sort(key=lambda x: float(x.edge_bps), reverse=True)
            top = structural_candidates[0]
            if len(structural_candidates) > 1:
                second = structural_candidates[1]
                conflict_gap_bps = float(self.config["signal"].get("structural_conflict_gap_bps", 80))
                if top.side != second.side and abs(float(top.edge_bps) - float(second.edge_bps)) <= conflict_gap_bps:
                    if debug is not None:
                        debug["drop_reason"] = "STRUCTURAL_CONFLICT"
                        debug["top_signal"] = top.signal_type
                        debug["second_signal"] = second.signal_type
                    return None
            structural = top
        predictions: list[dict[str, Any]] = []
        model_degraded = False
        llm2 = None
        llm_error = ""
        try:
            llm1 = self.llm1.infer(
                market_prob=market_prob,
                features={
                    "depth_imbalance": quote.get("depth_imbalance", 0),
                    "sigma_5m": quote.get("sigma_5m", 0),
                },
            )
            predictions.append(
                {
                    "model_name": "llm1",
                    "fair_prob": llm1.fair_prob,
                    "confidence": llm1.confidence,
                    "rationale": llm1.rationale,
                    "risk_tags": llm1.risk_tags,
                }
            )
            llm2 = self.llm2.validate(
                market_prob=market_prob,
                fair_prob=llm1.fair_prob,
                features={"spread_bps": quote.get("spread_bps", 0)},
            )
            predictions.append(
                {
                    "model_name": "llm2",
                    "fair_prob": llm2.fair_prob,
                    "confidence": llm2.confidence,
                    "rationale": llm2.rationale,
                    "risk_tags": llm2.risk_tags,
                }
            )
        except (LlmProviderError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            model_degraded = True
            llm_error = str(exc)
            predictions.append(
                {
                    "model_name": "llm_error",
                    "fair_prob": None,
                    "confidence": 0.0,
                    "rationale": llm_error[:400],
                    "risk_tags": ["llm_degraded"],
                }
            )

        if llm2 is None and structural is None:
            if debug is not None:
                debug["drop_reason"] = "NO_STRUCTURAL_AND_LLM_UNAVAILABLE"
            return None

        edge_bps_raw = (llm2.fair_prob - market_prob) * 10_000 if llm2 is not None else 0.0
        if structural is not None:
            edge_bps_raw = float(structural.edge_bps)
        costs = estimate_cost_bps(self.config, quote)
        expected_edge_after_costs_bps = edge_bps_raw - costs.total_bps

        if structural is None and llm2 is not None and expected_edge_after_costs_bps < self.config["signal"]["enter_edge_bps"]:
            if debug is not None:
                debug["drop_reason"] = "EDGE_BELOW_ENTER_THRESHOLD"
                debug["expected_edge_after_costs_bps"] = expected_edge_after_costs_bps
            return None

        confidence = min(p["confidence"] for p in predictions if p["confidence"] is not None)
        if structural is None and confidence < self.config["signal"]["min_confidence"]:
            if debug is not None:
                debug["drop_reason"] = "LOW_CONFIDENCE"
                debug["confidence"] = confidence
            return None

        signal_type = structural.signal_type if structural else "llm_edge"
        side = structural.side if structural else ("buy_yes" if edge_bps_raw > 0 else "buy_no")
        edge_bps = structural.edge_bps if structural else edge_bps_raw
        reason = structural.reason if structural else (llm2.rationale if llm2 else "llm_unavailable")
        if model_degraded:
            reason = f"{reason} | llm_degraded"

        created_at = utc_now_iso()
        signal_id = self._signal_id(market["market_id"], strategy_version, created_at[:16])
        if debug is not None:
            debug["drop_reason"] = None
            debug["signal_type"] = signal_type
            debug["model_degraded"] = model_degraded

        return {
            "signal_id": signal_id,
            "market_id": market["market_id"],
            "strategy_version": strategy_version,
            "signal_type": signal_type,
            "side": side,
            "market_prob": market_prob,
            "fair_prob": llm2.fair_prob if llm2 is not None else None,
            "edge_bps": edge_bps,
            "expected_edge_after_costs_bps": expected_edge_after_costs_bps,
            "confidence": confidence,
            "reason": reason,
            "status": "generated",
            "created_at_utc": created_at,
            "cost_breakdown": {
                "fee_bps": costs.fee_bps,
                "slippage_bps": costs.slippage_bps,
                "chain_bps": costs.chain_bps,
                "total_bps": costs.total_bps,
            },
            "predictions": predictions,
            "model_degraded": model_degraded,
        }
