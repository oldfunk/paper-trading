# questions.md — 员工 AI 疑问与阻塞（追加式，勿删他人条目）

## 2026-10-27 · T5' 反驳审查完成，含 2 条 P0（详见 `report-t5.md`）

**需要你处理（不是我在等的答复，是决策前提已塌）**：

1. **[P0] §7-B「vnpy（交易执行）+ rqalpha（回测）双框架」的事实前提被实测推翻**：
   `pip install vnpy`（3.13，隔离 venv）得到的 4.5.0 核心包里**没有任何撮合引擎**
   （`vnpy.trader` 下无 matcher；`vnpy_ctastrategy`/`vnpy_paperaccount`/`vnpy_riskmanager`
   均 ModuleNotFoundError），而撮合插件 `vnpy_ctastrategy/backtesting.py` 的成本/撮合入参只有
   `rate/slippage/size/pricetick`（期货式），**无 T+1、无涨跌停、无卖出单边印花税、无整手**。
   同时 `rqalpha` 有 `simulation_event_source.py` + `matcher/bar_matcher.py` +
   `utils/price_limits.py` + `sys_accounts`(T+1) —— 它**能做模拟盘撮合**，方案 §2 写的
   「仅回测（本地无实盘撮合）」不成立。⇒ 双框架既无必要，又正面违反路线图纪律 3。
   **请重新向用户告知并再拍一次 §7-B/§7-C**（C 的"全线降 3.13"只是为 vnpy 服务）。

2. **[P0] 选型表漏 `akquant`（AKFamily 出品，与本仓数据源 akshare 同 org）**：MIT、v0.3.71
   （2026-10-22，近 7 天发 5 版）、无 Qt/无 TA-Lib 依赖、文档明确支持 A 股 T+1
   （`tplusone=True`、`Position.available` 可用持仓）。综合契合度疑高过现方案，建议补进 M0 spike。

**我在等的答复（不阻塞，可批量回）**：

3. 迁移期 `paper_account.db` 与 MERGE_GUIDE §2 表契约的关系：方案 §4 让历史"只读归档 + 切换日
   新开账"，但面板/审计/`nav_history`/`op_log` 都直接读该库。归档后旧数据由谁提供查询接口？
   （影响我下一轮是否要补"归档只读视图"的任务草案。）
4. 依赖许可落地口径：若保留 rqalpha（非商业条款），README/CONTRACT/MERGE_GUIDE 需要加
   「组合不可商用分发」声明——这是文档改动（我可做，`docs(worker):`），但措辞要你定。
5. spike 边界确认：`rqalpha download-bundle` 在我的隔离 venv 里跑了 >4 分钟未完成（未计入结论）。
   任务箱要求跑通 rqalpha 回测 hello——是否允许我下载 bundle（≈数百 MB 到 `~/.rqalpha`）？
   还是改用本地 SQLite 自定义 `AbstractDataSource` 直接跑（更贴近 M1 真实路径，但要我先写适配器样例）。

**我这边的状态**：不改产品代码；不动 Pi/secrets/cron；spike 全部在
`C:\Users\yanzh\venvs\refactor-spike` 隔离 venv 内。
