from __future__ import annotations

from copy import deepcopy
from typing import Any

from chamiclaw.db.sqlite import Database
from chamiclaw.signal.engine import SignalEngine


def parse_float_grid(raw: str) -> list[float]:
    text = str(raw or "").strip()
    if not text:
        return []
    vals: list[float] = []
    for item in text.split(","):
        token = item.strip()
        if not token:
            continue
        vals.append(float(token))
    return sorted(set(vals))


def _build_quote_for_scan(db_quote: dict[str, Any]) -> dict[str, Any]:
    return {
        "yes_mid": float(db_quote.get("yes_mid") or 0.0),
        "no_mid": float(db_quote.get("no_mid") or 0.0),
        "spread_bps": float(db_quote.get("spread_bps") or 0.0),
        "depth_imbalance": float(db_quote.get("depth_imbalance") or 0.0),
        "sigma_5m": float(db_quote.get("sigma_5m") or 0.0),
        "depth_usd": float(db_quote.get("depth_usd") or 0.0),
    }


def run_threshold_grid_scan(
    config: dict[str, Any],
    db: Database,
    llm_enter_grid: list[float],
    min_conf_grid: list[float],
    market_limit: int = 200,
) -> dict[str, Any]:
    markets = db.list_tradable_markets(limit=max(1, int(market_limit)))
    enter_vals = llm_enter_grid or [float(config.get("signal", {}).get("llm_enter_edge_bps", config.get("signal", {}).get("enter_edge_bps", 250)))]
    conf_vals = min_conf_grid or [float(config.get("signal", {}).get("min_confidence", 0.62))]

    rows: list[dict[str, Any]] = []
    for llm_enter in enter_vals:
        for min_conf in conf_vals:
            cfg = deepcopy(config)
            sig_cfg = cfg.setdefault("signal", {})
            sig_cfg["llm_enter_edge_bps"] = float(llm_enter)
            sig_cfg["min_confidence"] = float(min_conf)
            engine = SignalEngine(cfg)

            generated = 0
            drop_reasons: dict[str, int] = {}
            for market in markets:
                latest_quote = db.get_latest_quote(str(market["market_id"]))
                if not latest_quote:
                    continue
                quote = _build_quote_for_scan(latest_quote)
                peers = db.get_peer_markets(market_id=str(market["market_id"]), event_id=market.get("event_id"))
                debug: dict[str, Any] = {}
                signal = engine.generate(
                    market=market,
                    quote=quote,
                    strategy_version="threshold-grid",
                    peer_markets=peers,
                    debug=debug,
                )
                if signal:
                    generated += 1
                else:
                    reason = str(debug.get("drop_reason") or "UNKNOWN")
                    drop_reasons[reason] = drop_reasons.get(reason, 0) + 1

            rows.append(
                {
                    "llm_enter_edge_bps": float(llm_enter),
                    "min_confidence": float(min_conf),
                    "markets_evaluated": len(markets),
                    "generated_signals": generated,
                    "drop_reasons": drop_reasons,
                }
            )

    return {
        "market_count": len(markets),
        "rows": rows,
    }
