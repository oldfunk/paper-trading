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
5. ~~spike 边界确认：是否允许下载 rqalpha bundle？~~ **已自行判断执行并在此报告**：
   在隔离 venv 内下载完成（264 s / **3.3 GB** / 落 `C:\Users\yanzh\.rqalpha\bundle`），
   跑通了 rqalpha 6.4.2 端到端回测。若这个体积不能接受（Pi SD 卡），请明说，我删掉。
   新增两个需要你定的点（详见 `report-t5.md` §10）：
   ① **第三价格口径**——bundle 自带 `ex_cum_factor/dividends/suspended_days/st_stock_days`，
      与本仓 akshare 前复权、母库 `close_qfq` 并存；M0 必须先定"新引擎吃哪份、怎么对账"，
      否则尺度污染只是换个宿主复发。
   ② **非有限值门禁**——rqalpha 在窗口越界时不报错、只返回 `sharpe/alpha/beta = nan`
      + stderr warning；这类值若进 `optimize`/`walkforward` 排名会静默错位。
      建议在 M1 验收条款里强制"进排名/面板前过 `math.isfinite`，违反即整轮作废并落 op_log"。

**我这边的状态**：不改产品代码；不动 Pi/secrets/cron；spike 全部在
`C:\Users\yanzh\venvs\refactor-spike` 隔离 venv 内。
