# ChamiClaw

Polymarket 分钟级量化系统（实验级）最小可运行骨架（推进到 T1）：

- Gamma API → Market Service（5 分钟）
- CLOB WS/REST → Price Engine（30 秒聚合）
- Brave Search → Info Engine（10 分钟 + 异常触发）
- Mode Engine（A / B_ALLOWED / NO_TRADE）
- Strategy Engine（3 分钟轮询）
- Risk Engine（独立否决）
- Execution Adapter（默认 Simmer，可替换）
- Portfolio Engine（成交驱动）
- Runtime Orchestrator + Cadence Scheduler（T1）

## 快速启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn chamiclaw.api.app:app --reload
```

## 配置（T1）

可通过环境变量切换仓储后端：

- `REPOSITORY_BACKEND=memory|sqlite`（默认 `memory`）
- `SQLITE_PATH=data/chamiclaw_t1.db`
- `SCHEDULER_ENABLED=true|false`（默认 `false`）
- `LOG_LEVEL=INFO|DEBUG|...`（默认 `INFO`）

## API（当前最小实现）

- `GET /health`
- `GET /ops/state`（查看内存/仓储运行状态，含 risk_controls）
- `GET /ops/config`（查看当前运行配置）
- `POST /ops/tick`（执行一次完整编排周期）
- `POST /ops/risk/reset`（重置风控冷却/日内停机状态）
- `POST /ops/trade-stats/reset`（重置交易计数）
- `POST /ops/portfolio/apply-pnl`（注入已实现PnL并更新组合状态）
- `POST /ops/state/reset`（清空运行态缓存信号，可选清空市场池/交易统计/风控控制位）
- `POST /markets/rank`
- `POST /price/ingest`
- `POST /info/analyze`
- `POST /mode/decide`
- `POST /strategy/run`

## 目录

- `src/chamiclaw/core/models.py`: 核心数据模型（Pydantic v2）
- `src/chamiclaw/engines/`: 市场、价格、信息、状态机、策略、风控、执行、组合引擎
- `src/chamiclaw/orchestration/runtime.py`: T1 编排器（market/info/mode/strategy loop）
- `src/chamiclaw/orchestration/scheduler.py`: 运行节奏任务注册 + 可选 APScheduler 启停
- `src/chamiclaw/adapters/simmer.py`: 默认执行适配器
- `src/chamiclaw/storage/repository.py`: 内存仓储（后续替换 PostgreSQL / DuckDB）
- `sql/schema.sql`: 最小表结构草案
- `tests/`: 关键规则单测

## 运行节奏（默认）
- Price Engine: 30 秒
- Strategy Loop: 3 分钟
- Market Service: 5 分钟
- Info Engine: 10 分钟（可事件触发）
