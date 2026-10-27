# 一周代码重审清单（2026-10-20 ~ 2026-10-27，事故驱动）

> 背景：2026-10-27 价格尺度事故（v0.7.1/v0.7.2 执行侧取母库后复权价，
> 以 175.97 买入真实价 22.24 的票，虚付 ~15,372；已冲正 + v0.7.5 门禁）。
> 事故根因不是单点 bug，是三类**系统性缺口**，本清单按类排查全周 40 提交/~2400 行。
> 进度回写本文件；里程碑级结论同步 `docs/ROADMAP.md`（唯一进度真相源）。
>
> 纪律：每项必须有**证据**（对抗性测试 / 脚本输出 / Pi 实测），不许“读了一遍觉得没问题”。
> 发现问题 → 域内直接修（小步提交），接缝 → 母库 Issue，跨阶段结论 → ROADMAP 勾选。

## 事故复盘（为什么测试没拦住）— R0

- [ ] R0.1 写事故复盘文档 `docs/incident-2026-10-27.md`：时间线、五道防线为什么全漏
  （计划价对的、执行价污染、涨跌停带同源自洽、金额上限被抬过地板、无跨源校验）
- [ ] R0.2 提炼“事故类”检查模式，写进 AGENTS.md（新数据字段进执行链必须过 sanity 门禁；
  新配置字段必须有接线测试）

## P0 — 同类根因全量排查（价格/数据尺度，事故直接同源）

- [ ] R1.1 **价格消费点全量清单**：`rg` 所有读 `price_map`/`close`/`bar_source`/`get_bars`
  的位置，逐个回答“价格从哪来、什么复权口径、和谁比过尺度”，产出表进本文件附录
- [ ] R1.2 **所有乘价格的运算**（金额=价×量）：市值、NAV、`max_order_value` 校验、
  费用地板、涨跌停带、条件单 `cond_value`、`risk_stop`、引擎 `min_ticket`——
  每处确认引用价与被校验价同源，且有量级 sanity（价格 ≤0、>10 万拒）
- [ ] R1.3 **母库外部输入验 sanity**（跨仓接缝类）：`integration/mother.py` 9+ 接口，
  每个返回值进 AI 上下文/执行链前的量级校验；`data_provenance` 已有 source/asof，
  补“口径”标注（adj=qfq/hfq/raw 已在 daily_bars，其他接口核对）
- [ ] R1.4 **AI 上下文里的价格与决策价一致性**：`llm_context.recent_closes`、
  `portfolio.total_value`、`price_map` 三处必须同口径；构造对抗测试：喂 hfq 数据 →
  AI 决策价被 `max_price_deviation` 门禁拦下（现有单测只覆盖执行层，补上下文层）
- [ ] R1.5 **回放对照**：`agent replay` 增加价格口径检查（决策时 price_source vs
  回放时 bar 口径不一致 → 显式告警），用 10-27 事故日做回放实测
- [ ] R1.6 Pi 实测：`data coverage/compare` 对全部持仓 + 股票池逐票量级 sanity
  （母库 qfq vs 本地 qfq 比值 0.9–1.1 之外的全部列出来人工核）

## P0 — 配置接线断层（require_stop 教训的全量排查）

- [ ] R2.1 **AgentConfig 字段接线审计**：反射列出 `AgentConfig` 全部字段 →
  逐个确认 cli.py `AgentConfig(...)` 有没有读 `agent_kwargs`、config.yaml 有没有键、
  有没有测试覆盖；漏一个补一个（当天已补 require_stop/require_basis/
  max_price_deviation，脚本化防复发）
- [ ] R2.2 **接线回归测试**：写 `test_config_wiring.py`——用反射对比
  “AgentConfig 字段集合 ⊆ cli.py 传参集合 ⊆ config.yaml 文档化键”，锁死防再断
- [ ] R2.3 **其他配置类同查**：`engine.local.yaml`/`strategy.local.yaml`/`config.yaml`
  各节键与读取代码对照（`data.daily_source`、`agent.event_*`、风险参数表），
  产出“配置键 → 读取点 → 默认值”对照表进本文件

## P1 — 执行链/下单链重审（loop.py +821 行）

- [ ] R3.1 `_execute_actions` 全分支走查：10+ 拒单分支的判断顺序是否合理
  （尺度门禁位置、basis 校验先于金额校验等）、有无绕过路径
- [ ] R3.2 `_execute_plan` vs `run()` 两条路径差异对照：from_plan 兜底价、
  dry-run、T+1 解冻、日亏熔断在两路径行为是否一致（对照测试）
- [ ] R3.3 计划生命周期：`save_plan`/`get_executable_plan`/过期消费/同日幂等/
  `agent_decisions` 落库时机——构造“跨停机/跨日/计划日非交易日”用例
- [ ] R3.4 条件单四条 AI 路径（计划/现决/事件复盘/cond design）统一性：
  `parse_conditions_verbose(holdings=...)` 是否处处传 holdings、
  `parse_rejected` 有无静默丢弃（本周 #29 这类假价格条件如何更早发现）
- [ ] R3.5 **条件单也加尺度门禁**：`cond_value` vs 挂单时现价偏离 >1.5× → 进
  `parse_rejected`（本次 #29 止盈 180 vs 现价 22 挂了 26 分钟才被人工发现）

## P1 — 母库集成层重审（mother.py +739 行，全新增）

- [ ] R4.1 逐接口降级矩阵测试补全：9+ 接口 × {母库好/表缺失/该票缺/库整体坏}
  4 态，现有合成库测试覆盖了几个组合，缺口列出来补
- [ ] R4.2 TTL 缓存与 `_seq` 连接键正确性：缓存键含 schema 版本吗（母库加列后
  旧缓存会不会吐旧口径数据）——`reset_cache` 触发条件审计
- [ ] R4.3 只读保证审计：全文件搜写操作嫌疑（`INSERT/UPDATE/DDL`、非 `mode=ro`
  连接、`SELECT *`），单测断言连接串参数
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
- [ ] R8.3 `rowsC` 折叠渲染的 XSS：新面板（provenance 表）的字段是否全走 `esc()`
  （母库数据是外部输入！）
- [ ] R8.4 错误信息泄漏审计：异常栈/路径/Key 有没有进 HTTP 响应或日志

## P3 — 文档一致性 + 收尾

- [ ] R9.1 AGENTS.md/README/config.yaml 注释 与代码实况对照（本周改动频繁，
  规则 3.2、AI 规则 5/6、配置节……逐条 diff）
- [ ] R9.2 本清单附录：R1.1 价格消费点表 + R2.3 配置键对照表
- [ ] R9.3 汇总报告：发现的问题数/已修数/遗留数 → ROADMAP 进度行 + 必要时子项目 Issue
- [ ] R9.4 复盘 R0.1 文档定稿 + 母库侧经验回传（若接缝问题 → 母库 Issue）

## 执行纪律

1. 顺序：R0 → P0（R1/R2）→ P1 → P2 → P3；P0 未完成不并行开 P1。
2. 每项完成 = 改动 + 测试 + 勾选；只审不改的要写“为什么不改”。
3. 小步提交打 tag（v0.7.6 起），每个 P 级收尾跑全量 + Pi 部署验证。
4. 发现新事故级问题（账本错/安全）：立即停下按事故流程处理，不继续清单。
