# Polymarket Minute System Implementation Notes

## Scope
- Real client scaffolding for Gamma/CLOB/Brave.
- Simmer execution adapter with live mode and dry-run guardrail.
- SQLite repository upgraded with order/fill/trade-log persistence and replay window summary.
- Phase gate service and runtime gating for MODE_B.
- API extension for phase evaluation, dry-run toggles, replay summary, and metrics summary.

## Runtime Defaults
- `EXECUTION_DRY_RUN=true`
- `PHASE_GATE_ENABLED=true`
- Phase 2 promotion requires:
  - `PHASE1_MIN_TRADES=200`
  - `PHASE1_MIN_WIN_RATE=0.57`
  - `PHASE1_MIN_RR=1.1`
  - `PHASE1_MAX_DRAWDOWN=0.05`

## API Additions
- `GET /ops/phase`
- `POST /ops/phase/evaluate`
- `POST /ops/dry-run/set`
- `POST /ops/replay/run`
- `GET /ops/metrics/summary`

## Verification Commands
- `python -m pytest -q`
- `ruff check .`
- `uvicorn chamiclaw.api.app:app --reload`
