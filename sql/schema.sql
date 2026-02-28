CREATE TABLE IF NOT EXISTS markets (
  market_id TEXT PRIMARY KEY,
  question TEXT NOT NULL,
  end_time TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL,
  tags JSONB NOT NULL,
  rule_text TEXT NOT NULL,
  rule_summary TEXT NOT NULL,
  resolution_sources JSONB NOT NULL,
  rule_clarity_score DOUBLE PRECISION NOT NULL,
  liquidity_score DOUBLE PRECISION NOT NULL,
  market_score DOUBLE PRECISION NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS price_snapshots (
  ts TIMESTAMPTZ NOT NULL,
  market_id TEXT NOT NULL,
  best_bid DOUBLE PRECISION NOT NULL,
  best_ask DOUBLE PRECISION NOT NULL,
  mid DOUBLE PRECISION NOT NULL,
  spread DOUBLE PRECISION NOT NULL,
  last DOUBLE PRECISION NOT NULL,
  depth_topk_json JSONB NOT NULL,
  volume_1m DOUBLE PRECISION NOT NULL,
  trades_1m INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS price_signals (
  ts TIMESTAMPTZ NOT NULL,
  market_id TEXT NOT NULL,
  change_1m DOUBLE PRECISION NOT NULL,
  change_5m DOUBLE PRECISION NOT NULL,
  change_15m DOUBLE PRECISION NOT NULL,
  vol_ratio_15m DOUBLE PRECISION NOT NULL,
  spread_status TEXT NOT NULL,
  breakout_15m BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS info_signals (
  ts TIMESTAMPTZ NOT NULL,
  market_id TEXT NOT NULL,
  event_detected BOOLEAN NOT NULL,
  risk_score DOUBLE PRECISION NOT NULL,
  confirmation_level INTEGER NOT NULL,
  clarification_flag BOOLEAN NOT NULL,
  top_sources_json JSONB,
  extracted_claims_json JSONB
);

CREATE TABLE IF NOT EXISTS mode_states (
  ts TIMESTAMPTZ NOT NULL,
  market_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  reason_codes_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
  ts TIMESTAMPTZ NOT NULL,
  order_id TEXT PRIMARY KEY,
  market_id TEXT NOT NULL,
  side TEXT NOT NULL,
  action TEXT NOT NULL,
  order_type TEXT NOT NULL,
  limit_price DOUBLE PRECISION,
  size_usd DOUBLE PRECISION NOT NULL,
  status TEXT NOT NULL,
  mode TEXT NOT NULL,
  dry_run BOOLEAN NOT NULL DEFAULT TRUE,
  adapter TEXT NOT NULL,
  raw_json JSONB
);

CREATE TABLE IF NOT EXISTS fills (
  ts TIMESTAMPTZ NOT NULL,
  order_id TEXT NOT NULL,
  market_id TEXT NOT NULL,
  fill_price DOUBLE PRECISION NOT NULL,
  fill_size DOUBLE PRECISION NOT NULL,
  fee DOUBLE PRECISION NOT NULL,
  raw_json JSONB
);

CREATE TABLE IF NOT EXISTS positions_snapshots (
  ts TIMESTAMPTZ NOT NULL,
  equity DOUBLE PRECISION NOT NULL,
  cash DOUBLE PRECISION NOT NULL,
  positions_json JSONB NOT NULL,
  daily_pnl DOUBLE PRECISION NOT NULL,
  consecutive_losses INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_compensations (
  idempotency_key TEXT PRIMARY KEY,
  payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS phase_gate_states (
  ts TIMESTAMPTZ NOT NULL,
  phase TEXT NOT NULL,
  allowed_mode_b BOOLEAN NOT NULL,
  reasons_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_logs (
  ts TIMESTAMPTZ NOT NULL,
  market_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  entry_ts TIMESTAMPTZ,
  exit_ts TIMESTAMPTZ,
  entry_price DOUBLE PRECISION,
  exit_price DOUBLE PRECISION,
  spread_entry DOUBLE PRECISION,
  spread_exit DOUBLE PRECISION,
  event_risk DOUBLE PRECISION,
  confirmation_level INTEGER,
  pnl DOUBLE PRECISION,
  holding_time INTEGER,
  reason_json JSONB
);
