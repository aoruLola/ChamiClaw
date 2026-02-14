# ChamiClaw：Polymarket 自动交易系统设计（双 LLM 辅助）

> 目标链路：**自动扫描市场 → 发现优势 → 下单 → 记录 → 复盘校准**
> 
> 本文只做系统设计，不执行交易。

---

## 0) 项目基线

## 0.1 配置管理
统一使用 `config.yaml` + `.env`：

- `.env`：密钥、API、私钥、Webhook
- `config.yaml`：策略阈值、风控参数、调度频率、仓位规则

建议结构：

```yaml
env: prod
timezone: UTC

network:
  chain: polygon
  rpc_url: ${RPC_URL}

apis:
  gamma_base: https://gamma-api.polymarket.com
  clob_base: https://clob.polymarket.com

scan:
  interval_sec: 60
  min_liquidity_usd: 100000
  min_depth_usd: 20000
  min_time_to_expiry_min: 180
  max_time_to_expiry_days: 30

signal:
  enter_edge_bps: 250
  exit_edge_bps: 80
  no_trade_zone_bps: 120
  min_confidence: 0.62

risk:
  per_market_pos_pct: 0.015
  event_cluster_exposure_pct: 0.10
  daily_max_drawdown_pct: 0.04
  max_spread_bps: 120
  pre_expiry_add_position_block_min: 120

execution:
  limit_order_only: true
  order_timeout_sec: 45
  min_reprice_interval_sec: 20
  max_retries: 3
```

## 0.2 结构化日志（JSON）
统一 JSON Line（UTC 时间）：

```json
{
  "ts_utc": "2026-02-14T01:00:00Z",
  "market_id": "123",
  "signal_id": "sig_abc",
  "action": "PLACE_LIMIT_BUY",
  "price": 0.57,
  "qty": 120,
  "reason": "edge_after_costs>0 and risk_pass",
  "result": "submitted",
  "latency_ms": 182
}
```

## 0.3 数据库（SQLite）

### 核心表
- `markets`：市场基础信息、规则摘要、可交易状态
- `quotes`：盘口、mid、深度、成交、衍生指标
- `trades`：订单、成交、成本、PnL、状态
- `predictions`：LLM1/LLM2 概率、置信度、解释

### 建议补充表
- `positions`：当前持仓快照
- `signals`：信号生命周期（生成→通过风控→下单）
- `audit_events`：异常、停机、人工干预

## 0.4 幂等与重试
- 每个信号生成唯一 `signal_id`（哈希：market_id + ts_bucket + strategy_version）
- 下单前检查 `trades` 中是否已存在该 `signal_id` 活动订单
- 重试策略：指数退避 + 幂等键，不重复成交

## 0.5 时间标准
- DB 全部 `UTC`
- 显示层本地化（可选）

---

## 1) 数据层

## 1.1 市场扫描
流程：
1. 拉取活跃市场
2. 过滤：流动性、规则清晰度、到期窗口
3. 写入 `markets`

过滤规则建议：
- `liquidity >= min_liquidity_usd`
- 规则摘要质量达标（见 1.3）
- 到期时间在策略窗口内

## 1.2 行情采集
定时采样：bid/ask/mid、深度、最近成交

衍生指标：
- `spread_bps`
- 深度不平衡 `depth_imbalance`
- 短窗波动率 `sigma_5m`

写入 `quotes`，并按 `market_id + ts_utc` 建索引。

## 1.3 规则摘要
每个市场做规则卡片（机器可读）：
- 结算时间
- 官方数据源
- 触发条件与歧义点
- “可交易评分”

**默认保守原则**：规则不清晰 => `tradable=false`

---

## 2) 信号层

## 2.1 结构信号（优先级最高）
1. **YES+NO 偏离 1**（含费后）
2. **跨市场概率矛盾**（同事件不同市场）
3. **期限结构异常**（短端/长端不一致）

先跑结构信号，减少 LLM 成本和幻觉影响。

## 2.2 双 LLM 模型信号

- **LLM1（生成器）**：输出 `fair_prob` 与主要驱动因子
- **LLM2（审校器）**：检验逻辑一致性，输出 `confidence` 与风险标签

核心公式：

- `edge = fair_prob - market_prob`
- `expected_edge_after_costs = edge - fee - slippage - chain_cost`

决策区间：
- `enter`: `expected_edge_after_costs >= enter_threshold`
- `exit`: edge 回落到 `exit_threshold`
- `no_trade_zone`: 两者之间不操作

低置信度缩仓：
- `position_scale = clamp((confidence - min_conf) / (1 - min_conf), 0, 1)`

---

## 3) 风控层（独立 Risk Engine）

所有订单都必须调用：`risk.check(order_intent)`

强制规则：
- 单市场仓位 ≤ 1–2%
- 同类事件敞口 ≤ 10%
- 单日最大回撤触发停机
- 禁止追价与大 spread 成交
- 到期前限制加仓

拒单时：
- 返回 `reject_code`
- 结构化写日志 + 入 `audit_events`

---

## 4) 执行层

## 4.1 订单管理
- 限价优先（默认禁用市价）
- 超时撤单
- 最小改价间隔
- `signal_id` 防重复下单

状态机：
`NEW -> SUBMITTED -> PARTIAL/FILLED/CANCELED/REJECTED`

## 4.2 对账机制
定期对账（例如每 5 分钟）：
- 拉交易所成交与持仓
- 与本地 `trades/positions` 比较
- 不一致则修复并记录
- 严重偏差触发 `PAUSED`

## 4.3 成本模型
下单前必须估算：
- 手续费
- 滑点（按深度模拟）
- 链上成本

仅当：`expected_edge_after_costs > 0` 才允许进场。

---

## 5) 评估层

## 5.1 回测
- 重放 `quotes`
- 简化撮合（价到即成，含滑点）
- 输出：收益率、最大回撤、Sharpe、盈亏比、胜率

## 5.2 模拟交易（Paper Trading）
- 只记信号，不下真实单
- 评估信号在 `5m/30m/24h` 的后验表现

## 5.3 校准
对 `predictions` 做概率分桶（例如 0.05 桶宽）：
- 预测概率 vs 实际命中率
- 使用 isotonic/Platt 做校准
- 产出校准曲线版本，写入策略版本记录

---

## 6) 运维与安全

## 6.1 告警与断路器
运行状态机：
- `RUNNING`
- `PAUSED`（可自动恢复）
- `HALTED`（需人工干预）

触发条件示例：
- API 连续失败
- 对账不一致
- 当日回撤超限
- 异常高频下单

## 6.2 密钥隔离
- 交易私钥仅在执行服务可见
- 分离研究服务与执行服务权限
- 生产环境最小权限原则

## 6.3 策略版本化
每次策略变更记录：
- `strategy_version`
- 参数快照
- 影响范围
- 回测结果摘要

---

## 7) 推荐系统架构（双 LLM）

```text
[Scanner] -> [Market Filter + Rule Summarizer] -> [Quote Collector]
                                       |-> markets/quotes

[Signal Engine]
  |- Structural Signals
  |- LLM1 FairProb
  |- LLM2 Validator + Confidence
  -> signal candidates

[Risk Engine] (hard gate)
  -> pass -> [Execution Engine] -> [Exchange/CLOB]
  -> reject -> [Audit Log]

[Reconciliation] -> 修正 trades/positions
[Evaluator] -> backtest/paper/calibration -> 新策略版本
```

---

## 8) 交易策略建议（落地优先级）

### Phase A（先稳）
- 只做结构信号（YES+NO 偏离 + 成本过滤）
- 全部 paper trading
- 建立日志、对账、回测闭环

### Phase B（再加双 LLM）
- LLM1 给 fair_prob，LLM2 做一致性审查
- 低置信度缩仓
- 仍以限价、小仓位执行

### Phase C（规模化）
- 引入跨市场矛盾与期限结构
- 分层资金管理（主仓/试错仓）
- 策略版本 A/B + 自动校准

---

## 9) MVP 里程碑（建议）

1. **第 1 周**：数据层 + SQLite + JSON 日志
2. **第 2 周**：结构信号 + 风控 Gate + 模拟执行
3. **第 3 周**：对账与告警状态机
4. **第 4 周**：双 LLM 接入 + 校准报告

交付标准：
- 任意订单可追溯（signal -> risk -> order -> fill -> pnl）
- 出现异常自动暂停
- 每日生成策略健康报告

---

## 10) 最小目录建议

```text
ChamiClaw/
  SYSTEM_DESIGN.md
  config/
    config.yaml
  src/
    scanner/
    signal/
    risk/
    execution/
    reconcile/
    evaluate/
  sql/
    schema.sql
  logs/
  data/
  reports/
```

> 以上设计可直接作为实现蓝图。先跑 paper，再逐步实盘，不建议一步到位自动化。