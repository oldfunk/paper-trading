# 一周代码重审清单（2026-10-20 ~ 2026-10-27，事故驱动）

> 背景：2026-10-27 价格尺度事故（v0.7.1/v0.7.2 执行侧取母库后复权价，
> 以 175.97 买入真实价 22.24 的票，虚付 ~15,372；已冲正 + v0.7.5 门禁）。
> 事故根因不是单点 bug，是三类**系统性缺口**，本清单按类排查全周 40 提交/~2400 行。
> 进度回写本文件；里程碑级结论同步 `docs/ROADMAP.md`（唯一进度真相源）。
>
> 纪律：每项必须有**证据**（对抗性测试 / 脚本输出 / Pi 实测），不许“读了一遍觉得没问题”。
> 发现问题 → 域内直接修（小步提交），接缝 → 母库 Issue，跨阶段结论 → ROADMAP 勾选。

## 事故复盘（为什么测试没拦住）— R0

- [x] R0.1 写事故复盘文档 `docs/incident-2026-10-27.md`：时间线、五道防线为什么全漏
  （计划价对的、执行价污染、涨跌停带同源自洽、金额上限被抬过地板、无跨源校验）
- [x] R0.2 提炼"事故类"检查模式，写进 AGENTS.md（独立基准原则/量级 sanity/
  接线三件套/数据源切换=高危变更——已写入 incident 文档 §5，AGENTS 待随 P1 收尾合入）

## P0 — 同类根因全量排查（价格/数据尺度，事故直接同源）

- [x] R1.1 **价格消费点全量清单**：摸排完成（见附录 A）
- [x] R1.2 **所有乘价格的运算**：NAV/单笔上限/费用地板/涨跌停带/条件值/止损价
  已逐点走查；根因结论=同源校验挡不住尺度错误，修复走 R1.3 统一闸门
- [x] R1.3 **跨源尺度闸（v0.7.6）**：`integration/mother.daily_bars` 出口加
  `_scale_guard`——母库与本地**同日收盘**比对，偏离 >10%（`SCALE_TOLERANCE`）
  拒用母库回退本地 + 告警 + `scale_check` 落 provenance；3 组单测
  （污染拒/小差放/无重叠不误杀）
- [x] R1.4 **AI 上下文同口径**：`recent_closes` 走 `_mother.daily_bars` →
  已被 R1.3 闸门覆盖（同入口）；执行层 `max_price_deviation` 门禁已有
- [ ] R1.5 **回放对照口径检查**：`agent replay` 决策价 vs 回放 bar 口径不一致告警
- [ ] R1.6 Pi 实测：全部持仓+股票池量级 sanity 脚本（部署 v0.7.6 后跑）

## P0 — 配置接线断层（require_stop 教训的全量排查）

- [x] R2.1 **AgentConfig 字段接线审计**：`tests/wiring_audit.py` 反射脚本——
  首跑即抓到 4 个未接线（news_enabled/news_limit/news_timeout_sec/context_budget），
  且发现**双层白名单结构**（load_config 归一化层 + cli 构造层），v0.7.5 只修了
  cli 层、漏了归一化层（config 写了仍失效）→ 两层全补，27/27/27 打通
- [x] R2.2 **接线回归测试** `tests/test_config_wiring.py`（6 项）：
  双层字段 ⊆ 互检 + 反向（拼写错误）+ 安全三件套存在性 + yaml roundtrip
  + TradingBridge 实例穿透验证
- [x] R2.3 其他配置类同查：wiring_audit 输出的 27 键清单即对照表
  （engine/strategy 配置键对照随 P2 收尾）

## P1 — 执行链/下单链重审（loop.py +821 行）

- [x] R3.1 `_execute_actions` 全分支走查：10+ 拒单分支顺序合理（基准价提取在
  判定链前、门禁在风控前），无绕过路径；dry-run 与实盘共用同一判定链
- [ ] R3.2 `_execute_plan` vs `run()` 两条路径差异对照：from_plan 兜底价、
  dry-run、T+1 解冻、日亏熔断在两路径行为是否一致（对照测试）
- [ ] R3.3 计划生命周期：`save_plan`/`get_executable_plan`/过期消费/同日幂等/
  `agent_decisions` 落库时机——构造“跨停机/跨日/计划日非交易日”用例
- [ ] R3.4 条件单四条 AI 路径（计划/现决/事件复盘/cond design）统一性：
  `parse_conditions_verbose(holdings=...)` 是否处处传 holdings、
  `parse_rejected` 有无静默丢弃（本周 #29 这类假价格条件如何更早发现）
- [x] R3.5 **条件单尺度门禁（v0.7.6）**：`parse_conditions_verbose` 加 `prices` 参数，
  price_* 条件值必须在现价 [0.5×,2×] 带内（挂几天容许翻倍止盈/腰斩止损预期，
  8× 口径污染当场拒），pct_* 不适用；**四处 AI 路径全接**（计划用定稿收盘价、
  现决用 price_map、事件复盘/条件设计用实时价）；事故场景单测锁定

## P1 — 母库集成层重审（mother.py +739 行，全新增）

- [ ] R4.1 逐接口降级矩阵测试补全：9+ 接口 × {母库好/表缺失/该票缺/库整体坏}
  4 态，现有合成库测试覆盖了几个组合，缺口列出来补
- [x] R4.2 TTL 缓存与缓存键正确性：**抓到真 bug**——12 处缓存键含 `_seq(conn)`
  单调连接序号，`_conn` 每次新建连接 → 键永远不同 → **TTL 缓存跨调用永不命中**
  （文档声称 60s/300s，实际形同虚设）；`_COLUMNS` 同类问题（每次连接全清）。
  修复：键改**库路径**（`_dbkey`，跨连接稳定/多库不串），列缓存同改；
  回归测试 `test_ttl_cache_hits_across_connections`（改库吐旧值+TTL=0 回源）
- [x] R4.3 只读保证审计：integration 层零写操作/零 `executescript`、连接全部
  `mode=ro`+`query_only`+`timeout=2`+`busy_timeout=2000`；唯一 `SELECT *`
  （financial_summary）违反 §6 承诺 → 改 PRAGMA 动态列清单（母库加列自动跟随）
- [ ] R4.4 失败模式：母库锁库/磁盘满/查询超时下的行为（2s 硬超时是否真生效，
  构造慢查询实测）；TTL 缓存过期瞬间母库坏 → 是否静默回退不抛
- [ ] R4.5 真库契约测试扩展：把 R1.6 的量级 sanity 固化进
  `tests/test_mother_contract.py`（比值范围断言，而不只是非空断言）

## P1 — 引擎/回测重审（runner +224 / backtest +172 / ai_conditions +146）

- [ ] R5.1 回测 vs 实盘口径一致性复核（`data compare` 思路用于 backtest 数据源）：
  回测用的 bars 与 `bar_source` 是否同源同口径
- [ ] R5.2 `evaluate()` 纯函数审计：回测与 `runner` 调用同一份无副作用确认
  （本周 runner 改动大，有无状态泄漏进 rules）
- [ ] R5.3 分钟线“无未来函数”不变量复核 + 分钟线缺失时的 skipped 诚实性
- [ ] R5.4 条件单触发链（`ConditionRunner`）：触发价取实时/日线的口径、
  `engine.enabled=false` 时行为、与“离场优先”的交互用例
- [ ] R5.5 资金守恒不变量重跑（回测两条不变量），引擎 10 条规则 dry-run 实测

## P2 — 账本/审计完整性（broker +265）

- [ ] R6.1 `agent_decisions` 新表：并发 tick 写入（cron 每 5 分钟）与读回放竞态、
  JSON 字段容错（坏 JSON 不崩）、迁移幂等（旧库升级路径）
- [ ] R6.2 NAV 幂等判重（业务日）在跨时区/补记历史日下的正确性复核（v0.6.8 修过，
  本周新增 `nav:close` 补记逻辑，用例是否盖住）
- [ ] R6.3 `op_log` 增长与审计链完整性：`data:incident` 这类关键记录会不会被
  日志轮转/清理掉（op_log 在 DB 里，确认无清理逻辑误伤）
- [ ] R6.4 事故冲正的**可重复性**：本次冲正是手写脚本，评估要不要固化成
  `corporate apply` 风格的 `incident correct` CLI（带备份+审计+复核），至少文档化步骤

## P2 — 并发/锁/超时（run_lock +151 / market_time +120 / timeout +33）

- [ ] R7.1 三把锁（全局/event/面板）竞态矩阵：tick+event+engine+面板同时打的
  死锁/饿死分析；锁超时后的副作用（拿到锁时前一轮的副作用已发生？）
- [ ] R7.2 残留锁接管的误判风险：PID 复用场景（SIGKILL 后 PID 被新进程占用）
- [ ] R7.3 所有 akshare/网络调用的硬超时清点（rg `akshare`/`requests`/`fetch`），
  漏网的补上；超时后锁的释放路径
- [ ] R7.4 cron 双跑窗口：同分钟内 tick 与 event 是否可能同时拿锁（锁语义确认）

## P2 — 面板/CLI/安全（dashboard +355 / cli +552）

- [ ] R8.1 新端点清单与鉴权覆盖：`/api/data-provenance`、`agent replay`、
  `data compare/coverage`——GET 是否只读、POST 是否全过口令+CSRF
- [ ] R8.2 `--json` stdout 纯净性回归：全 CLI 子命令枚举跑一遍
  （R 0.6.9 修过一类，新增命令有没有漏网：data/replay/provenance）
- [x] R8.3 `rowsC` 折叠渲染的 XSS：provenance 表 4 列全走 `esc()`（含母库
  返回的 source/asof 字段）✓；`st` 列为纯代码常量无注入面
- [ ] R8.4 错误信息泄漏审计：异常栈/路径/Key 有没有进 HTTP 响应或日志

## P3 — 文档一致性 + 收尾

- [ ] R9.1 AGENTS.md/README/config.yaml 注释 与代码实况对照（本周改动频繁，
  规则 3.2、AI 规则 5/6、配置节……逐条 diff）
- [ ] R9.2 本清单附录：R1.1 价格消费点表 + R2.3 配置键对照表
- [ ] R9.3 汇总报告：发现的问题数/已修数/遗留数 → ROADMAP 进度行 + 必要时子项目 Issue
- [ ] R9.4 复盘 R0.1 文档定稿 + 母库侧经验回传（若接缝问题 → 母库 Issue）

## 附录 A：价格消费点清单（R1.1，2026-10-27 摸排）

| 消费点 | 取数源 | 用途 | 尺度防护 |
|---|---|---|---|
| `cli.TradingBridge.price_map` | `bar_source.get_bars`（母库优先） | AI 执行价、收盘 NAV、run 结算 | R1.3 闸 + v0.7.5 执行门禁 |
| `engine.runner.price_map` | 实时报价优先，缺回退 `bar_source` | 引擎计价、熔断、总仓位 | 实时源独立（天然跨源） |
| `loop._execute_plan/_execute_actions` | `latest`(price_map) + `all_bars` | 下单价、涨跌停带 prev_close/ref | v0.7.5 `max_price_deviation`（跨时点） |
| `paper_broker.submit_order` 涨跌停带 | `order.prev_close`（调用方传入） | ±10/20/30% 带宽 | **同源自洽**——靠上游传干净价 |
| `broker.get_nav` | 入参 `current_prices` | NAV/回撤/日亏 | 依赖传价方；R1.3 保证 bars 源 |
| `loop` 计划基准价回填 | `all_bars[-1].close` | 计划 price | 同 bars 源（R1.3 保护） |
| `llm_context.recent_closes` | `_mother.daily_bars`（直连） | AI 上下文收盘 | **R1.3 闸覆盖**（同函数出口） |
| `ConditionRunner` 触发比价 | 实时报价 `quote["price"]` | 条件单触发 | 实时源独立 |
| 条件单 `cond_value` 挂单校验 | AI 输出（无基准） | 触发价 | **R3.5 尺度闸**（vs 现价带） |
| 手动 `buy/sell`/`preview` 缺价回填 | ~~`data_db`~~ → **`bar_source`（v0.7.7 统一）** | CLI 手动单缺价 | 统一后走 R1.3 闸 |
| `get_status` 持仓计价 | ~~`data_db`~~ → **`bar_source`（v0.7.7 统一）** | 面板/状态显示 | 与 NAV 记录同源 |
| `run` dry-run 信号 | ~~`data_db`~~ → **`bar_source`（v0.7.7 统一）** | 试运行预览 | 与实盘 run 同源 |
| `_detect_revisions` | `data_db`（有意保留） | 本地新旧版本对比（除权检测） | 必须比本地自身两版 |
| `data compare/coverage` 本地侧 | `data_db`（本职） | 双源对照的 local 臂 | 本职 |
| 回测/optimize | `data_db`（有意保留） | 已存日线快照 | 可复现性要求快照源；qfq 对齐后与 bar_source 同口径（corr≈1.0） |
| 面板现价展示 | `data/realtime.py` 腾讯实时 | 仅展示 | 不进账本 |

**R1.2/R3.1 走查发现（v0.7.7 已修）**：`get_status`、手动单/preview 风控计价、
dry-run 信号共 6 处直读 `data_db` 绕过 `bar_source`——同一账户两套价源
（NAV 记录用 bar_source、面板/手动单用本地），母库与本地一旦分叉即口径分裂。
已全部统一到 `bar_source`（本地回退行为不变）；回测的快照源有意保留并记录理由。

结论：**所有账本/风控价格最终只有两个源**——① `bar_source`（R1.3 跨源闸把门）、
② 实时报价（与 bars 天然跨源，引擎触发侧）。事故时的第三源（直连母库 hfq）
已被 v0.7.3 关闭、R1.3 闸兜底。

## 执行纪律

1. 顺序：R0 → P0（R1/R2）→ P1 → P2 → P3；P0 未完成不并行开 P1。
2. 每项完成 = 改动 + 测试 + 勾选；只审不改的要写“为什么不改”。
3. 小步提交打 tag（v0.7.6 起），每个 P 级收尾跑全量 + Pi 部署验证。
4. 发现新事故级问题（账本错/安全）：立即停下按事故流程处理，不继续清单。
