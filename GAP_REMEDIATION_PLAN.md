# ChamiClaw 差距修复清单（代码实检版）

更新时间：2026-02-14（UTC）  
依据：对 `src/chamiclaw`、`sql/schema.sql`、`config/*` 的实际代码检查与 CLI 实跑结果（doctor/status/reconcile/backtest/calibrate/report）。

目标：把当前“可运行骨架”推进到“可控小额实盘”。

---

## 一、当前状态快照（本次更新）

### 1) 完整度评分（当前）
- 工程骨架完整度：**85%**
- 研究/模拟功能完整度：**60%**
- 实盘交易功能完整度：**25%**
- 综合可用度：**55%**

### 2) 已验证可用（CLI 实跑）
- `doctor`：可用（配置/DB/schema/角色检查通过）
- `status`：可用（可返回 markets/quotes/signals/orders/audit）
- `reconcile`：可用（当前返回 mismatch=0）
- `backtest`：可用（可输出回测指标）
- `calibrate`：可用（可输出 calibration 报告并生成策略版本）
- `report`：可用（可输出日报）

### 3) 关键现状问题（2026-02-14 最新实测）
- `run-once` 当前可运行（近期未复现卡死），但仍需继续观察长跑稳定性。
- 当前统计：`markets=220`、`quotes=3487`、`signals=2`、`orders=0`、`audit_events=180`。
- 最新 `run-once` 返回：`scanned_markets=132`、`quotes_written=132`、`signals_generated=2`、`orders_submitted=0`。
- 信号掉落已细分为（最新）：
  - `EDGE_AFTER_COST_BELOW_THRESHOLD=12`
  - `RAW_EDGE_BELOW_THRESHOLD=7`
- 已完成一版 reachability 修复：research 模式阈值放松（仅 dry-run 生效），信号由 0 提升到可产出（稳定 2-3 条/轮）。
- Go/No-Go 已升级并通过最新验证（在研究策略门槛下）：`final_verdict=GO`。
- 新增 `live-readiness` / `deploy-readiness` / `clob-smoke` 预检命令，用于阻断“研究模式 GO 误判为可实盘”，并验证 py-clob-client 认证链路。
- 执行层新增 live precheck 拦截：已支持 `py-clob-client` 后端，若缺私钥/L2凭证或非 `execution` 角色将拒单，避免盲目实盘请求。
- 新增循环预算保护（signal/execution 阶段）+ `RUN_ONCE_TIMING` 审计记录，降低 watchdog SIGKILL 风险并可定位慢阶段。
- 新增 LLM 降级熔断：单周期降级信号超过阈值将自动 `PAUSED`（`ops.llm_degrade_pause_threshold`）。
- 最新联调现状：硅基流动接口可达，但账户余额不足导致 LLM 返回 30001（进而出现 `NO_STRUCTURAL_AND_LLM_UNAVAILABLE` 掉落）。
- 结论：当前主要阻塞在 **edge 与阈值/成本口径**，并非单纯风控或执行失败。
- 结论：目前仍适合“数据与研究验证”，不适合直接实盘。

### 4) 最新优先级（按阻塞程度）
1. **信号触发可达性修复（进行中）**
   - [x] 主流程接入 `peer_markets`，`cross_market` / `term_structure` 已可参与
   - [x] 拆分并记录 drop reason 指标（edge/cost/confidence/conflict）
   - [x] research 模式阈值放松（仅 dry-run）
   - [x] live 模式独立阈值（`live_llm_enter_edge_bps` / `live_min_confidence`）
   - [ ] 继续提升有效样本与成本后正 edge 占比
2. **成本模型与阈值一致性修复（仍高优先）**
   - 明确 fee/slippage/chain 的口径与单位（bps）
   - 对 `expected_edge_after_costs_bps` 增加审计日志，避免“过度扣减”误伤
   - 给 research 模式提供可回放的参数扫描（阈值网格）
3. **Go/No-Go 判定增强（避免“0信号也GO”）**
   - 新增最小信号产出门槛（例如最近 N 个周期 `signals_generated >= X`）
   - 新增最小有效样本门槛（edge 分布、成本后 edge 正占比）
   - 将“流程健康”与“交易有效”拆分为双 verdict
4. **真实 LLM 接入替代 mock**（并保证失败降级）
5. **真实执行闭环**（下单/查单/撤单/状态归一）
6. **真实风险与对账输入**（去掉占位值，接真实仓位与成交）

---

## 二、当前实现状态总览

- ✅ **已具备**：基础架构、SQLite、JSON日志、状态机、风险检查入口、基础扫描与信号流水线
- ⚠️ **部分具备**：结构信号扩展函数已写，但主引擎未完整接入；告警/演练可用但未制度化联动
- ❌ **关键缺口**：真实LLM调用、真实交易执行闭环、真实对账、真实风险输入、配置统一（YAML/.env 与运行一致）

---

## 二、按优先级的修复清单（不含工时）

## P0（阻塞实盘）

### P0-1 双LLM落地（当前仍为 Mock）
**现状（代码）**
- `src/chamiclaw/llm/interfaces.py` 中 `Llm1Generator/Llm2Validator` 为规则型 mock，不是外部 LLM 调用。

**修复项**
- 接入真实 LLM Provider（模型、超时、重试可配置）
- LLM1 输出：`fair_prob/rationale`
- LLM2 输出：`confidence/risk_tags` 与一致性审校
- 失败降级：仅结构信号，主流程不中断
- 写入 `predictions` 表（目前主流程未写该表）

**验收标准**
- 推理异常不会中断主流程
- 预测结果可追溯（请求ID/模型名/输出）
- `predictions` 写入覆盖率达标

---

### P0-2 执行层真实化（当前为 dry-run）
**现状（代码）**
- `src/chamiclaw/execution/executor.py` 的 `place_limit_order()` 默认模拟提交。

**修复项**
- 接 CLOB 实际下单、查单、撤单
- 完成订单状态机闭环：`new/submitted/partial/filled/canceled/rejected`
- 支持超时撤单、最小改价间隔、重试上限
- 严格幂等防重（signal_id + 客户端幂等键）

**验收标准**
- 可完成“下单→状态更新→撤单”全链路
- 无重复成交
- 订单状态可追踪到最终态

---

### P0-3 风控输入真实化（当前含占位值）
**现状（代码）**
- `ExecutionEngine.build_order_intent()` 中 `position_pct/cluster_exposure_pct/daily_drawdown_pct` 等仍是固定占位值。

**修复项**
- 从 `positions/trades/orders` + 交易所实时仓位构建真实风险输入
- 日内回撤与同类事件敞口统一计算口径
- 拒单原因结构化落库（audit）

**验收标准**
- 每次 `risk.check` 的输入均可回放
- 拒单日志包含阈值与实际值

---

### P0-4 配置系统统一（当前运行主路径仍偏 JSON）
**现状（代码）**
- `src/chamiclaw/settings.py` 当前只支持 JSON 读取，和文档里 `.env + YAML` 基线不一致。

**修复项**
- 统一配置加载顺序：`.env -> config.yaml -> CLI 覆盖`
- 保留 JSON 兼容作为过渡
- 敏感信息仅走环境变量

**验收标准**
- YAML 可直接运行全流程
- key/secret 不在仓库明文配置中

---

## P1（提升策略质量）

### P1-1 结构信号完整接入主引擎
**现状（代码）**
- `signal/structural.py` 已有 `cross_market`、`term_structure` 函数；
- 但 `signal/engine.py` 当前主流程实际只用 `detect_pair_cost_signal()`。

**修复项**
- 将跨市场矛盾、期限结构信号纳入 `SignalEngine.generate()` 主路径
- 支持按配置开关与阈值调参
- 增加信号去噪与冲突决策逻辑

**验收标准**
- 三类结构信号都可独立产生日志与统计
- 可按信号类型回测对比

---

### P1-2 行情与成本模型真实化
**现状（代码）**
- `quote_collector.py` 中 spread/depth 为合成近似，未接真实 orderbook 深度。

**修复项**
- 接真实盘口深度（bid/ask level）
- 滑点模型基于可成交深度
- 成本拆分：手续费 + 滑点 + 链成本
- 强制 `expected_edge_after_costs > 0` 才可下单

**验收标准**
- 每笔信号可输出成本分解
- 回放时成本估计可复现

---

### P1-3 对账引擎真实化
**现状（代码）**
- `cli.py` 的 `cmd_reconcile` 仍是示例比较（本地数据拷贝作为“交易所数据”）。

**修复项**
- 接交易所真实持仓/成交
- 差异分级处理（自动修复 / 人工介入）
- 超阈值自动 `PAUSED`

**验收标准**
- 能识别常见差异并给出动作
- 对账异常可自动触发状态迁移

---

## P2（评估与运维闭环）

### P2-1 Paper/Backtest 精细化
**现状（代码）**
- `evaluate/paper.py` 和 `evaluate/backtest.py` 逻辑较简化，缺少撮合与排队细节。

**修复项**
- 增强简化撮合（延迟、滑点、部分成交）
- 补齐 5m/30m/24h 信号后验统计
- 引入策略版本维度统计

**验收标准**
- 报告输出收益/回撤/胜率/盈亏比
- 可按策略版本对比

---

### P2-2 校准闭环
**现状（代码）**
- 有校准命令与输出文件，但“模型输出->校准->参数更新”闭环不完整。

**修复项**
- 统一 `predictions` 与 `paper_results` 关联
- 支持多校准方法切换
- 生成“参数建议”并落策略版本表

**验收标准**
- 每日有可审计校准产物
- 参数变更前后表现可对比

---

### P2-3 告警与演练制度化
**现状（代码）**
- `ops/alerting.py`、`ops/drill.py` 已可用；但与主流程联动和巡检制度不足。

**修复项**
- 告警等级规范（INFO/WARN/CRITICAL）并接入关键失败点
- 固化演练清单（API失败/对账异常/回撤超限）
- 演练结果进 `reports/drills.jsonl` 并纳入日报

**验收标准**
- 关键故障都可触发预期状态迁移
- 有固定频率的演练记录

---

## 三、上线前 Go/No-Go 门槛

满足以下条件才允许从 paper 切到小额真单：
1. 对账连续稳定，无严重差异遗留
2. 重复下单事件为 0
3. 风控拒单可完整追溯（输入值、阈值、原因）
4. 所有成交前都经过 `expected_edge_after_costs > 0` 检查
5. 双LLM失败降级在压力下可稳定触发

> 建议：先小额、低频、限价，再逐步放量。

---

## 进展更新（2026-02-14 持续推进）

- 已补充并接入：
  - 结构信号冲突决策（同向优先、反向接近则抑制）与配置阈值
  - 强制 `expected_edge_after_costs_bps > 0` 风控门禁
  - 风控拒单结构化详情（实际值/阈值）写审计
  - 真实 orderbook 采集通道（有数据则使用，无数据回退合成报价）
  - 对账异常告警（WARN/CRITICAL）与日报 drill 汇总字段
  - 交易所响应适配层（order/orderbook/positions 统一解析）与 `go-no-go` 门槛检查命令
  - 交易所 endpoint 配置化（`apis.clob_endpoints` + env 覆盖）与 `run-once` 运行时预算保护（防长周期卡死）
  - 交易所字段映射配置化（`apis.clob_field_map`）+ `clob_profile` 默认映射（default/legacy_v1），并接入执行/行情/对账链路统一解析
  - `run-once` 信号丢弃原因统计（drop reasons）+ 日报展示 latest_run_once/go_no_go
  - `llm-fallback-check` 门槛校验命令（强制 LLM 失败下验证结构降级稳定性）
  - 信号触发可达性修复：`signal.llm_enter_edge_bps`（默认 80 bps，未配置回退 `enter_edge_bps`）+ drop 诊断阈值字段
  - 测试基线修复：数据库连接上下文关闭与事务提交语义恢复；风险用例对齐 `NEGATIVE_EDGE` 优先级
  - 联调压测编排命令：`validate-go-no-go`（run-once/reconcile/llm-fallback-check/go-no-go 串联）并输出 `reports/go_no_go_validation.json`
  - 日报联动：`report` 新增 `latest_go_no_go_validation` 摘要字段（若验证报告存在）
  - Go/No-Go 判定增强落地：新增 `flow_verdict` / `trading_verdict` 双 verdict，交易有效性门槛纳入 `recent_run_once_cycles/recent_signals_generated/edge_positive_after_cost_ratio`；并统一 `go-no-go` 与 `report` 命令读取 `go_no_go` 配置口径
  - `run-once` 改为“两阶段流水线”（先全量市场/quote，再统一信号/执行），确保 `peer_markets` 在主流程可用，`cross_market/term_structure` 可拿到同事件同周期上下文
  - 信号丢弃审计增强：`SIGNAL_DROP` 记录 `drop_category(edge/cost/confidence/conflict/availability)` 与 `raw_edge_bps/expected_edge_after_costs_bps/threshold_bps/cost_breakdown`
  - research 参数回放命令：`threshold-grid`（`llm_enter_edge_bps` × `min_confidence` 网格扫描）并输出 `reports/threshold_grid.json`

- 待继续深化（仍未完成的“最后一段路”）：
  - 无（Go/No-Go 五项联调压测已具备可重复命令与报告证据链）    
