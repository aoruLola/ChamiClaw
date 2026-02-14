# ChamiClaw

A from-scratch implementation of the Polymarket automated trading architecture described in `SYSTEM_DESIGN.md`.

## Scope

- Scanner: fetches active markets from Gamma API and applies liquidity/time/rule filters.
- Signal engine: structural arbitrage + mock LLM confidence fusion.
- Risk engine: hard gate before any order.
- Execution engine: limit-order workflow with dry-run mode.
- Reconciliation: local-vs-remote position checks.
- Evaluation: paper outcomes, calibration, backtest summary, daily report.

## Quick Start

```bash
# Initialize database schema
PYTHONPATH=src python3 -m chamiclaw.cli init-db --config config/config.yaml

# Run one full cycle
PYTHONPATH=src python3 -m chamiclaw.cli run-once --config config/config.yaml

# Check status
PYTHONPATH=src python3 -m chamiclaw.cli status --config config/config.yaml

# Generate outputs
PYTHONPATH=src python3 -m chamiclaw.cli backtest --config config/config.yaml
PYTHONPATH=src python3 -m chamiclaw.cli calibrate --config config/config.yaml
PYTHONPATH=src python3 -m chamiclaw.cli report --config config/config.yaml
```

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Commands

- `init-db`
- `run-once`
- `run-loop`
- `status`
- `reconcile`
- `state`
- `backtest`
- `calibrate`
- `report`
- `doctor`
- `llm-fallback-check`
- `go-no-go`
- `validate-go-no-go`
- `threshold-grid`
- `alert-test`
- `live-readiness`
- `deploy-readiness`
- `drill` (failure drill: `api-failure|reconcile-mismatch|drawdown-limit`, default dry-run)

## Notes

- `execution.dry_run=true` by default.
- `execution.backend` supports `py-clob-client` (recommended) or `rest`.
- Config load order: `.env -> config YAML/JSON -> CHAMICLAW_* env overrides`.
- LLM modes:
  - `llm.mode=mock` (default): deterministic local mock inference.
  - `llm.mode=http`: call `llm.llm1_endpoint` and `llm.llm2_endpoint` via JSON POST.
    - LLM1 expected response fields: `fair_prob`, optional `confidence`, `rationale`, `risk_tags`.
    - LLM2 expected response fields: `validated_fair_prob` or `fair_prob`, optional `confidence`, `rationale`, `risk_tags`.
- On LLM failure, strategy degrades to structural signals only (if available).
- Cost model uses `fee + depth-adjusted slippage + chain_cost_bps` and enforces edge-after-cost checks before model-only entries.
- LLM-only entries use `signal.llm_enter_edge_bps` (default `80`) as the model-entry threshold; if unset, it falls back to `signal.enter_edge_bps`.
- Signal drop diagnostics now include category + edge/cost details in audit events (`category='signal'`, `code='SIGNAL_DROP'`).
- Structural signal set now includes:
  - `pair_cost_arb`
  - `cross_market_divergence` (same event peer markets)
  - `term_structure_inversion` (same event different expiries)
- Live execution path (`execution.dry_run=false`) supports submit -> poll -> timeout-cancel via CLOB HTTP endpoints:
  - `POST /orders`
  - `GET /orders/{order_id}`
  - `POST /orders/{order_id}/cancel`
- Reconciliation now supports real exchange pull + local auto-repair:
  - configure `reconcile.exchange_positions_endpoint`
  - `chamiclaw reconcile` compares local/exchange positions and can transition system state.
- Calibration supports `isotonic` and `platt` modes (`evaluate.calibration_method`), emits parameter recommendations, and writes strategy versions into DB.
- Drill command supports batch mode: `drill --scenario all` and writes records to `reports/drills.jsonl`.
- Go/No-Go gate command: `chamiclaw go-no-go --config config/config.yaml`
  - outputs dual verdicts: `flow_verdict` (process health) + `trading_verdict` (signal/effective-edge readiness).
  - `verdict` is `GO` only when both verdicts are `GO`.
  - policy comes from `go_no_go` config:
    - `min_recent_cycles`
    - `min_recent_signals`
    - `min_edge_sample_size`
    - `min_edge_positive_ratio`
    - `max_llm_degrade_rate`
- Integrated Go/No-Go validation command:
  - `chamiclaw validate-go-no-go --config config/config.yaml --cycles 20 --reconcile-every 5 --fallback-iterations 50 --require-go-streak 3`
  - writes `reports/go_no_go_validation.json` with per-cycle run/reconcile/go-no-go evidence and final blockers.
- Threshold grid scan command (research mode):
  - `chamiclaw threshold-grid --config config/config.yaml --enter-grid 80,120,200 --confidence-grid 0.55,0.62,0.70 --market-limit 200`
  - writes `reports/threshold_grid.json` for threshold replay and drop-reason breakdown.
- LLM fallback pressure check command:
  - `chamiclaw llm-fallback-check --config config/config.yaml --iterations 50`
  - verifies structural fallback keeps producing signals when LLM is forced to fail.
- Exchange mapping supports profile-level defaults and custom overrides:
  - Endpoints: `apis.clob_profile` + `apis.clob_endpoints` (submit/status/cancel/orderbook/positions), with env overrides `CHAMICLAW_CLOB_*_ENDPOINT`.
  - Response fields: `apis.clob_field_map` for `order` / `orderbook` / `positions` aliases.
- `run-once` stability guard:
  - `scan.max_cycle_runtime_sec` caps per-cycle runtime (partial commit + WARN audit when exceeded).
  - `scan.max_orderbook_calls_per_cycle` limits orderbook requests.
  - `scan.orderbook_for_tradable_only` reduces non-tradable market overhead.
- Key isolation: set `CHAMICLAW_ROLE=research|execution` (default `research`).
  - `research` role cannot read `POLYMARKET_PRIVATE_KEY`.
  - `execution` role can read `POLYMARKET_PRIVATE_KEY` for order paths.
  - `doctor` prints `runtime_role` and `private_key_visible` for quick verification.
- Existing scripts under `polymarket-*` were used as references only; this implementation is standalone under `src/chamiclaw`.
