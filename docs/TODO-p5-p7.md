# P5/P6/P7 — 母库契合与 AI 决策深化（长线）

> 依据：`docs/subproject-protocol.md`（母整合/子施工；域内子项目修，接缝母项目修，Issue 是唯一跨仓通道）、
> `docs/mother-interface.md`（子项目接口契约）。
> 目标：让子项目真正“切到母库策略”跑起来，验证与母库结构是否契合；发现接缝问题按协议给母库提 Issue。

## P5 — AI 决策深化（组合级 + 止损门禁 + 回放）

- [x] P5.1 组合级约束进 prompt：总敞口/现金占比/行业集中度（`stock_snapshot.sector`）/相对基准（`benchmark_daily`）；`basis` 允许引用 `portfolio.*`
- [x] P5.2 止损门禁：`agent.require_stop=true` 时，买入缺 `risk_stop` 且无对应卖出条件 → 拒单；`risk_stop` 与引擎 `trailing_stop/time_stop` 口径对齐说明
- [ ] P5.3 AI 决策落库 `agent_decisions`（decision_date/plan_id/actions_json/basis/result）供回放与审计
- [ ] P5.4 决策回放/对照：CLI `agent replay --date` 用同一上下文重放 AI 决策 vs 规则，输出差异
- [ ] P5.5 AI 成绩校准：`analysis_outcome` 有 verdict 后接入（覆盖度低则 `unavailable`，不臆测）

## P6 — 母库数据补全适配与监控

- [x] P6.1 schema 对齐：`instrument`（symbol→instrument_id）、`kline_daily.market/instrument_id`、`trading_calendar`（空则回退本地日历）
- [x] P6.2 覆盖度监控：每接口 coverage/stale 汇总，CLI `data coverage`（面板 provenance 面板待做）
- [x] P6.3 母库行情口径：`close_adj` 为后复权、本地为前复权；`data compare` 量化差异
- [x] P6.4 契约测试扩展：`instrument`/`trading_calendar` 列校验

## P7 — 可观测/审计/跨仓协作

- [ ] P7.1 三表契约文件（拥有/只读/永不碰）供母项目 vendor 门禁
- [ ] P7.2 面板“数据来源”面板（source/asof/stale）
- [ ] P7.3 母库 Issue 提交（接口级）：qfq 缺失、`trading_calendar` 空、`market_state_daily` 未落库
- [ ] P7.4 契约测试纳入母项目 CI（接缝测试位）

## “切到母库策略”验证路径

1. `data compare`：同标的本地 qfq vs 母库 hfq 的 MA5/MA20 交叉差异 → 量化口径影响。
2. `agent run --dry-run --data-source mother`：用母库日线喂信号，与本地结果对照（默认仍本地，开关式）。
3. 差异写入 `op_log`（`data:compare`）与 `docs/mother-interface.md` 结论；接缝问题提母库 Issue。
