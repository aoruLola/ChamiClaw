PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS markets (
  market_id TEXT PRIMARY KEY,
  event_id TEXT,
  slug TEXT,
  question TEXT NOT NULL,
  description TEXT,
  end_time_utc TEXT,
  liquidity_usd REAL DEFAULT 0,
  volume_usd REAL DEFAULT 0,
  rule_summary_json TEXT,
  tradable INTEGER DEFAULT 0,
  tradable_reason TEXT,
  updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quotes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market_id TEXT NOT NULL,
  ts_utc TEXT NOT NULL,
  yes_bid REAL,
  yes_ask REAL,
  no_bid REAL,
  no_ask REAL,
  yes_mid REAL,
  no_mid REAL,
  spread_bps REAL,
  depth_usd REAL,
  depth_imbalance REAL,
  sigma_5m REAL,
  raw_json TEXT,
  FOREIGN KEY (market_id) REFERENCES markets(market_id)
);
CREATE INDEX IF NOT EXISTS idx_quotes_market_ts ON quotes (market_id, ts_utc);

CREATE TABLE IF NOT EXISTS signals (
  signal_id TEXT PRIMARY KEY,
  market_id TEXT NOT NULL,
  strategy_version TEXT NOT NULL,
  signal_type TEXT NOT NULL,
  side TEXT NOT NULL,
  market_prob REAL NOT NULL,
  fair_prob REAL,
  edge_bps REAL,
  expected_edge_after_costs_bps REAL,
  confidence REAL,
  reason TEXT,
  status TEXT NOT NULL,
  created_at_utc TEXT NOT NULL,
  FOREIGN KEY (market_id) REFERENCES markets(market_id)
);
CREATE INDEX IF NOT EXISTS idx_signals_market_time ON signals (market_id, created_at_utc);

CREATE TABLE IF NOT EXISTS predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id TEXT,
  model_name TEXT NOT NULL,
  fair_prob REAL,
  confidence REAL,
  rationale TEXT,
  risk_tags_json TEXT,
  created_at_utc TEXT NOT NULL,
  FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
);

CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY,
  signal_id TEXT,
  market_id TEXT NOT NULL,
  side TEXT NOT NULL,
  limit_price REAL NOT NULL,
  quantity REAL NOT NULL,
  status TEXT NOT NULL,
  retries INTEGER DEFAULT 0,
  created_at_utc TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL,
  FOREIGN KEY (signal_id) REFERENCES signals(signal_id),
  FOREIGN KEY (market_id) REFERENCES markets(market_id)
);
CREATE INDEX IF NOT EXISTS idx_orders_market_status ON orders (market_id, status);

CREATE TABLE IF NOT EXISTS trades (
  trade_id TEXT PRIMARY KEY,
  order_id TEXT,
  market_id TEXT NOT NULL,
  side TEXT NOT NULL,
  fill_price REAL NOT NULL,
  fill_qty REAL NOT NULL,
  fee_usd REAL DEFAULT 0,
  slippage_bps REAL DEFAULT 0,
  pnl_usd REAL,
  ts_utc TEXT NOT NULL,
  FOREIGN KEY (order_id) REFERENCES orders(order_id),
  FOREIGN KEY (market_id) REFERENCES markets(market_id)
);

CREATE TABLE IF NOT EXISTS positions (
  market_id TEXT PRIMARY KEY,
  yes_qty REAL DEFAULT 0,
  no_qty REAL DEFAULT 0,
  avg_cost_yes REAL,
  avg_cost_no REAL,
  unrealized_pnl_usd REAL DEFAULT 0,
  updated_at_utc TEXT NOT NULL,
  FOREIGN KEY (market_id) REFERENCES markets(market_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc TEXT NOT NULL,
  level TEXT NOT NULL,
  category TEXT NOT NULL,
  code TEXT NOT NULL,
  message TEXT NOT NULL,
  context_json TEXT
);

CREATE TABLE IF NOT EXISTS strategy_versions (
  strategy_version TEXT PRIMARY KEY,
  created_at_utc TEXT NOT NULL,
  config_snapshot_json TEXT NOT NULL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS paper_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id TEXT,
  market_id TEXT NOT NULL,
  horizon_min INTEGER NOT NULL,
  entry_prob REAL NOT NULL,
  exit_prob REAL,
  realized_edge_bps REAL,
  created_at_utc TEXT NOT NULL,
  FOREIGN KEY (signal_id) REFERENCES signals(signal_id),
  FOREIGN KEY (market_id) REFERENCES markets(market_id)
);
