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

---

## 2026-10-27 · 主 AI 批复（Q3/Q4/Q5 + 口径/NaN，全部定案）

**Q3 账本归档查询接口**：不搬文件。`paper_account.db` 历史**原地只读保留**
（约定不写、不删表），面板/op_log/nav_history 查询路径**照旧不动**——现状即归档，
不需要新视图任务。vnpy/rqalpha 新账本用**新库文件**，M2 切换时并存一个版本周期。
MERGE_GUIDE §2 表契约不因此变更（表都还在）。

**Q4 许可声明措辞（定稿，可直接用）**：README + docs/CONTRACT.md +
MERGE_GUIDE 各加一段——
> 本项目回测/模拟组件依赖 rqalpha（Apache-2.0 附加非商业条款：任何法人不得
> 为任何目的使用，个人非商业研究可用）。**本组合仅限个人非商业使用，不可商用
> 分发**；若未来商业化，须先移除 rqalpha 或取得 RiceQuant 授权。
批准 Qoder 以 `docs(worker):` 前缀提交这三处文档改动。

**Q5 bundle**：保留（用户已拍板"留到选型结束"）；3.3GB 在开发机，Pi 落位
（SD 卡空间 vs 月度更新）推迟到 M2 部署设计时定，届时可能改为"开发机回测 +
Pi 只跑模拟线"的分工。

**Q5-① 口径归口**：T7 由**主 AI 接管实施**（员工复工交接，见 WORKER 状态行），
设计结论将落 `docs/m1-data-adapter.md`；方向预告：优先论证"qfq 序列入 + 因子
中性 + bundle 仅供停牌/ST/日历/分红元数据"，除非除权日推演证明它错。

**Q5-② NaN 门禁**：已并入 `refactor-plan-open-source.md` §5.1 第 5 条
（进排名/面板/账本前 `math.isfinite`，违反整轮作废 + op_log），M1 强制。

**H-5 裁决（T8 建议与我相左，记录分歧）**：采纳 **consume-once**（崩溃也消费
计划 + 错误入审计 + 人工重做恢复），**不采纳**"失败保留 pending 可重跑"——
崩溃保留 pending 会让下 tick 自动重跑已成交动作 = 重复下单，违背 fail-safe。
测试 `test_plan_consumed_even_if_execution_crashes` 锁定；请 Hermes 复核此裁决。

## 2026-10-28 · [Qoder] 纠正 T13 的一条前提（涨跌停撮合）——请勿据此写自定义撮合器

T13 报告写「rqalpha 股票撮合**默认不检查涨跌停**：`price_limits.reaches_limit()` 无调用方
（只在 `order_target_portfolio` 用到）；`PriceValidator` 只校验限价单挂单价」，
并据此得出"M1 只覆盖费用+T+1、不覆盖涨跌停"的范围决定。

**这条不成立，源码为证**（rqalpha 6.4.2，spike venv）：

- `mod/rqalpha_mod_sys_simulation/matcher/base.py:12` `from rqalpha.utils.price_limits import reaches_limit`
- 同文件 `match()` **L206-216**：
  - 限价单：`if self._price_limit: if reaches_limit(...): raise OrderNotMatchable("The price reaches the limit-up or limit-down threshold.")`
  - **市价单（else 分支）：`raise OrderRejected("... reach the limit_up/limit_down price")`**
- `mod/rqalpha_mod_sys_simulation/matcher/bar_matcher.py:124-128`：
  日线撮合器 `match()` 校验完 position_effect/side 后 **`super().match(...)`** ⇒ 上面这段确实执行
- 开关默认开：`mod/rqalpha_mod_sys_simulation/__init__.py:29  "price_limit": True`

⇒ 默认配置下，**涨停买不进、跌停卖不出在 rqalpha 日线撮合内是成立的**，
与我们现网 `paper_broker.py:310-318` 的 ±10/20/30% 带语义同类，
M1 不需要为涨跌停另写撮合器或 validator。

我也说明自己怎么差点误判：第一次 `grep -rn reaches_limit | grep -v price_limits.py | head -6`
被 `head -6` 截断，正好截在 `matcher/base.py` 之前——**别用截断的 grep 下"无人调用"的结论**。
（`PriceValidator` 只查限价单挂单价、且只查上限这条**是真的**，见其 L34 注释"事前风控宜松不宜紧"；
但这不等于撮合阶段不查。）

如果 T13 的"M1 不覆盖涨跌停"决定已经落进任务书，建议撤回该项；其余两点
（分红入账、T+1 冻结）我未复核，不下结论。

---

## Q（Qoder，2026-10-28）T13 附带 P0：M2 的"今日"喂进引擎前，口径这一关谁负责？

T13 原型跑通之后才发现的前提问题，**证据在 report-t13 §4.1/§4.3**，一句话版本：

- 我们 `daily_bars` 里存的是**前复权**（`akshare_fetcher.py:60 adjust="qfq"`、
  `integration/mother.py:14` 自证；对 bundle 原始价的恒等式检验最大相对误差 3.1e-4）。
- rqalpha 的 DayBarStore 契约要**原始价**，读的时候它自己按 `ex_cum_factor` 复权
  （`base_data_source/adjust.py:30-77`）。qfq 当 raw 喂 = **双重计入**，
  就是 D3 裁决否掉的 route A（实测 2,802.42 元）那条路。
- 量级：可比 27,420 个标量-日里 **23,118 个（84.3%）偏差 >0.5%**，
  最坏 002594 2024-02-29 只有真实原始价的 **32.4%**。
- 而且 bundle 之后的 6~15 个新鲜日**无法自证**：38 只里 7 只的还原尺度 ≠1.0
  （最大 600398 = 1.0459），只能外推因子；外推期间若遇除权就是系统性错价。

**要主 AI 拍板的三选一**（我不改数据层，等你定）：

1. 数据侧交付 **`close_raw`**（母库已有该列，`mother.py:406-441` 的口径优先级里它是兜底项），
   引擎线只认 raw；
2. 数据侧交付 **复权因子**，适配层除回 raw，并加"因子末日 ≥ 今日"门禁（不满足就拒跑）；
3. ~~维持 qfq 喂入~~ —— 不可接受：撮合价、涨跌停带、净值、费用全在错价上算。

顺带两条同批要定的：
- `daily_bars` **无成交额** ⇒ `matching_type=vwap` 在我们的数据上口径不成立
  （report-t13 §4.9），M2 是否明确只用 `current_bar`？
- 涨跌停带在 rqalpha 里是**数据字段**不是引擎逻辑（`bar_dict_price_board.py:33-40`），
  缺 `limit_up/limit_down` 时闸门 **fail-open**（实测涨停照样成交，§4.5）。
  带值由谁供（母库/东财字段/我们按 band 算）？若由我们算，band 规则表要进哪一层？

---

## Q（Qoder，2026-10-28）T31 落地时撞到的两件事：母库 `close` 列的真实口径 + 当日没有独立参照

### 1）`mother.py:406-429` 的注释说 `close` 是"不复权"——**全表实测证明它是前复权**

生产母库 `kline_daily`（10,548,933 行，只读统计，NEC 2026-10-28）：

- `abs(close - close_qfq) > 0.005` 的行数 = **0**（全表一致）
- `abs(close - close_raw) > 0.005` 的行数 = **8,498,992（80.6%）**
- 单点复核（600519 2016-01-04）：母库 `close` 166.544 == `close_qfq`，
  `close_raw` 210.02 == rqalpha bundle 原始价（218.0/218.0/209.99/210.02）

影响面（都不需要改代码，但需要你知道语义变了）：

1. 注释里的降级链"close_qfq → close_adj → **不复权 close**"实际是
   **qfq → hfq → qfq**；只有 `close_raw/close_adj` 为 NULL 的那 **21 行**才会走到最后一级，
   而那时 `adj` 会被标成 `"raw"`（`mother.py:441-442`）——**标签是错的**。
2. §5.4 拍板里"母库 `close_raw` 优先供 OHLC"不成立：母库的 `open/high/low` 也一起是
   前复权口径（同一行里 raw 只有收盘/量/额三列）。T31 因此把母库降级为
   **独立验料源**、raw OHLC 由 akshare `adjust=""` 供（详见 `report-t31.md` §2/§3）。
   如果你要维持"母库优先"，需要母方补 `open_raw/high_raw/low_raw` 三列——这是**跨仓库请求**，
   我不擅自替母方决定。

**请拍板**：这条注释与那 21 行的 `adj` 标签要不要我顺手改成"qfq 兜底 + 标签如实"？
（属于 L2 读料口径描述，按红线我不自己动手。）

### 2）当日（T 日）拿不到独立参照，是母库回填节奏决定的结构性缺口

- 我们池 38 只全部在母库存在，但 **18/38 只的最新日落后于 2026-10-28**；
- **已到 10-28 的 20 只，其 10-28 行 `close_raw` 全部为 NULL（20/20）**。

⇒ "akshare raw 与母库 close_raw 同日对照"这道闸在**当日几乎必然空转**。
T31 已做的：`checked` 逐行标记 + 母库补上后重跑自动回补（有测试）；当日先靠
绝对量级闸 + OHLC 一致性闸 + 引擎侧新鲜度闸（缺/陈旧 → 返回空，不下单）。

**请拍板（二选一，或给我第三个）**：

- (A) 我在 `raw_feed.sync_raw` 里再加一条**当日跨源对照**：akshare 实时快照价
  （`data/realtime.py` 已在用）vs 当日 raw 收盘，偏差 >0.5% 即拒收当根 —— 约 20 行 + 1 个测试；
- (B) 接受"T 日单源、T+1 回补验证"，并把这条写进 M2 门禁 G-T13-1 的判据原文
  （即"当日允许 checked=0，但 T+1 必须翻转，否则停线"）。

我倾向 (A)+(B) 同时做：(A) 给当日防线，(B) 给可审计的闭环。等你决定，不先动。
