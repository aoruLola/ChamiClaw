from __future__ import annotations

import hashlib
from typing import Any

from chamiclaw.llm.interfaces import Llm1Generator, Llm2Validator, LlmProviderError
from chamiclaw.signal.costs import estimate_cost_bps
from chamiclaw.signal.structural import (
    calc_pair_cost_edge_bps,
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
        peer_quotes_by_market_id: dict[str, dict[str, Any]] | None = None,
        debug: dict[str, Any] | None = None,
        enable_cross_market_signal: bool = True,
        enable_term_structure_signal: bool = True,
    ) -> dict[str, Any] | None:
        yes_mid = float(quote["yes_mid"])
        no_mid = float(quote["no_mid"])
        market_prob = yes_mid
        spread_bps = float(quote.get("spread_bps", 0.0) or 0.0)

        yes_bid = quote.get("yes_bid")
        yes_ask = quote.get("yes_ask")
        no_bid = quote.get("no_bid")
        no_ask = quote.get("no_ask")
        if any(v is None for v in (yes_bid, yes_ask, no_bid, no_ask)):
            # Compatibility fallback: derive synthetic BBO from mids and spread.
            half_spread_prob = max(0.0, spread_bps) / 10_000.0 / 2.0
            yes_bid = max(0.0, min(1.0, yes_mid - half_spread_prob))
            yes_ask = max(0.0, min(1.0, yes_mid + half_spread_prob))
            no_bid = max(0.0, min(1.0, no_mid - half_spread_prob))
            no_ask = max(0.0, min(1.0, no_mid + half_spread_prob))

        peers = peer_markets or []
        peer_quotes_map = peer_quotes_by_market_id or {}
        structural_candidates = []
        pair_calc = calc_pair_cost_edge_bps(yes_bid, yes_ask, no_bid, no_ask)
        pair = detect_pair_cost_signal(yes_bid, yes_ask, no_bid, no_ask, float(self.config["signal"].get("enter_edge_bps", 180)))
        pair_hit = pair is not None
        if pair_hit:
            structural_candidates.append(pair)
        cross_hit = False
        if enable_cross_market_signal and self.config["signal"].get("enable_cross_market_signal", True):
            peer_quotes = [peer_quotes_map.get(str(p.get("market_id"))) for p in peers if peer_quotes_map.get(str(p.get("market_id"))) is not None]
            cross = detect_cross_market_signal(
                market=market,
                quote=quote,
                peer_quotes=peer_quotes,
                gap_bps_threshold=float(self.config["signal"].get("cross_market_gap_bps", 150)),
            )
            cross_hit = cross is not None
            if cross_hit:
                structural_candidates.append(cross)
        term_hit = False
        if enable_term_structure_signal and self.config["signal"].get("enable_term_structure_signal", True):
            term = detect_term_structure_signal(
                market=market,
                quote=quote,
                peer_markets=peers,
                peer_quotes_by_market_id=peer_quotes_map,
                inversion_bps_threshold=float(self.config["signal"].get("term_structure_gap_bps", 120)),
            )
            term_hit = term is not None
            if term_hit:
                structural_candidates.append(term)
        if debug is not None:
            debug["pair_cost_hit"] = pair_hit
            debug["cross_market_hit"] = cross_hit
            debug["term_structure_hit"] = term_hit
            debug["pair_calc"] = pair_calc

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
                        debug["drop_category"] = "conflict"
                        debug["top_signal"] = top.signal_type
                        debug["second_signal"] = second.signal_type
                    return None
            structural = top
        predictions: list[dict[str, Any]] = []
        model_degraded = False
        llm2 = None
        llm_error = ""
        single_llm_mode = bool(self.config.get("llm", {}).get("single_llm_mode", False))
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
            if single_llm_mode:
                llm2 = llm1
            else:
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
                debug["drop_category"] = "availability"
            return None

        edge_bps_raw = (llm2.fair_prob - market_prob) * 10_000 if llm2 is not None else 0.0
        if structural is not None:
            edge_bps_raw = float(structural.edge_bps)
        costs = estimate_cost_bps(self.config, quote)
        signal_cfg = self.config.get("signal", {})
        min_confidence = float(signal_cfg.get("live_min_confidence", signal_cfg.get("min_confidence", 0.60)))
        confidence_values = [float(p["confidence"]) for p in predictions if p.get("confidence") is not None and p.get("model_name") != "llm_error"]
        if confidence_values:
            confidence = min(confidence_values)
        elif structural is not None:
            confidence = min_confidence
        else:
            confidence = 0.0

        signal_type = structural.signal_type if structural else "llm_edge"
        side = structural.side if structural else ("buy_yes" if edge_bps_raw > 0 else "buy_no")
        edge_bps_abs = abs(float(edge_bps_raw))
        raw_edge_threshold = float(signal_cfg.get("enter_edge_bps", 280))
        if structural is None:
            raw_edge_threshold = float(signal_cfg.get("llm_enter_edge_bps", raw_edge_threshold))
        expected_edge_after_costs_bps = edge_bps_abs - float(costs.total_bps)
        min_net_edge_bps = float(signal_cfg.get("min_net_edge_bps", 0.0))
        max_spread_bps = float(signal_cfg.get("max_spread_bps", 280))

        if edge_bps_abs < raw_edge_threshold:
            if debug is not None:
                debug["drop_reason"] = "RAW_EDGE_BELOW_THRESHOLD"
                debug["drop_category"] = "edge"
                debug["raw_edge_bps"] = edge_bps_abs
                debug["threshold_bps"] = raw_edge_threshold
                debug["cost_breakdown"] = {
                    "fee_bps": float(costs.fee_bps),
                    "slippage_bps": float(costs.slippage_bps),
                    "chain_bps": float(costs.chain_bps),
                    "total_bps": float(costs.total_bps),
                }
            return None

        if expected_edge_after_costs_bps < min_net_edge_bps:
            if debug is not None:
                debug["drop_reason"] = "NET_EDGE_TOO_LOW"
                debug["drop_category"] = "cost"
                debug["expected_edge_after_costs_bps"] = expected_edge_after_costs_bps
                debug["threshold_bps"] = min_net_edge_bps
                debug["cost_breakdown"] = {
                    "fee_bps": float(costs.fee_bps),
                    "slippage_bps": float(costs.slippage_bps),
                    "chain_bps": float(costs.chain_bps),
                    "total_bps": float(costs.total_bps),
                }
            return None

        if spread_bps > max_spread_bps:
            if debug is not None:
                debug["drop_reason"] = "SPREAD_TOO_WIDE"
                debug["drop_category"] = "risk"
                debug["spread_bps"] = spread_bps
                debug["threshold_bps"] = max_spread_bps
            return None

        if confidence < min_confidence:
            if debug is not None:
                debug["drop_reason"] = "LLM_CONFIDENCE_LOW"
                debug["drop_category"] = "confidence"
                debug["confidence"] = confidence
                debug["min_confidence"] = min_confidence
            return None

        edge_bps = structural.edge_bps if structural else edge_bps_abs
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
                "fee_bps": float(costs.fee_bps),
                "slippage_bps": float(costs.slippage_bps),
                "chain_bps": float(costs.chain_bps),
                "total_bps": float(costs.total_bps),
            },
            "predictions": predictions,
            "model_degraded": model_degraded,
        }
