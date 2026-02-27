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

## API（当前最小实现）

- `GET /health`
- `POST /ops/tick`（执行一次完整编排周期）
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
