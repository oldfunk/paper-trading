# T59 · 盘中分钟供给闭环（2026-11-03，Qoder）

派发原文（Batch 13）三条：① 主 AI 已装 `6,21,36,51 9-15` 的 `deploy/m3_minute_intraday.sh`；
② 验证 `m3 verify` 的 `minute_feed`/`heartbeat(minute)` 回绿、tick 日志出现自证；
③ 复核 `ensure_today_minutes()` 的触发路径与 `--intraday` 的 bar 时间戳语义；
④ 若 15 分钟粒度不足 → 评估加密到 5 分钟。**结论：粒度够，不需要加密；但供给链上查出三处缺陷，都已修。**

## 0. 一句话

盘中供给本身是通的（每跳 23–48 根、延迟 ≤1 分钟），真正在坏的是**标的归属**：
北交所 `920156` 被四处判成沪/深 ⇒ 分钟线永远 0 行、`op_log[minute:gap]` 一上午刷 92 条、
涨跌停带按主板 10% 算、意图在队列里"静默等待"到次日过期。

## 1. 逐条核销

| 派发条目 | 状态 | 证据（现网 / 本地） |
|---|---|---|
| ② `minute_feed` 回绿 | ✅ | 现网 `m3 verify`：`✅ 分钟线供给（新线命脉）：今天 883 行 / 40 只；最新 2026-11-03T11:25`（修之前是 0 行 / 39 只，`920156` 一根都没有） |
| ② `heartbeat(minute)` 回绿 | ✅ | `var/heartbeat.json`：`"minute": {"ts":"2026-11-03T11:51:02","ok":true,"detail":"written=0 errors="}`；修之前是 `"ok":false,"detail":"written=429 errors=920156"` |
| ② tick 日志的自证 | ✅（附一条修正） | `rq tick --json` 有 `minute_feed` 字段；**但 idle 分支不采**（无意图就不采，符合设计）。为了让"这一跳被什么挡下"看得见，新增 `instrument_unavailable` 自证字段（§3-C） |
| ③ 触发路径复核 | ✅ 两条路径同口径 | cron `minute sync --intraday` 与 `rq tick` 都调 `ensure_today_minutes()` ⇒ 同一个 `completed_slots()`（**严格早于当前分钟**才算收完）。现网实测：11:51 那跳 `written=0 missing=[]`（已齐 ⇒ 一次接口都不打）；11:26 手动删料后重采 `written=69 latest=2026-11-03T11:25` |
| ④ 15 分钟 vs 5 分钟 | **不用加密** | 钱类路径不依赖那条 cron：`rq tick` 每 5 分钟一跳且**本跳自己补料**。最坏情况推演：决策 11:16 ⇒ 需要 11:20 那根 ⇒ 11:21 的池级采集或 11:22 的 tick 自采都会带上，延迟 ≤ 一根 bar（5 分钟）。把池级采集改 5 分钟只会把新浪请求量翻三倍（38→40 只 ×12 次/小时），收益为零 |
| 顺手：重复/坏 cron 行 | 已清 | crontab 里有一条被 `--symbols \` 拼坏的重复行（每 15 分钟跑一次垃圾参数）。以备份为底本删掉，只留 `deploy/m3_minute_intraday.sh` 那条；备份 `/home/nec/crontab.pre-t60-20261103-112629.bak` |

## 2. 查出来的三处缺陷（都修了）

### A. 交易所归属在五个地方各判一遍，北交所 920 段全错

`920156`（海昌智能，池 11-02 入）在旧写法里：新浪分钟线取 `sh920156`（**0 行**）、
腾讯实时取 `sz920156`（空字段）、rqalpha 标的 `920156.XSHG`（挂错所）、
涨跌停带 **10%**（北交所是 30%）、akshare 日线 `KeyError 'date'` → 回落东财又被断连
⇒ **raw 日线 0 行**。同一时刻实测三个源都只认 `bj`：新浪实时 39 字段、腾讯 87 字段、
新浪 5 分钟 48 根；修完 `fetch_daily_raw("920156")` 直接回 10 行、`000426` 回 6 行。

收敛成 `utils/markets.py` 一张表（段表按**前两位**判，`900xxx` 沪市 B 股留 sh），
五处全部接过去。`tests/test_realtime.py` 里原来把 `920000 → sz` 当合同钉住的断言
按现网证据改写（母库 `tc_encode` 同一处错，见 `questions.md`）。

### B. 盘中采集把同一症状刷成 92 条流水

`minute sync --intraday` 每跳失败就 `op_log[minute:gap]` 一条；11-03 09:30–11:30 共
**92 条**（都是 `920156` 取错市场）。现在按 `gap_fingerprint(day, errors)` 去重：
**同日同症状只落一条，症状集合一变立刻再落**（不静默也不刷屏，与 T22 看门狗同套路）。
心跳仍每跳都写，采集器停了照样响。

### C. 标的不在 bundle 里 ⇒ 意图"静默等待"到明天

`instruments.pk` 28,063 只里 **`.BJ` 有 0 只**（`stocks.h5` 也只有 5,537 只沪深），
所以北交所标的新引擎**天生买不了**。旧路径是 `bar_dict[ob]` 抛异常被当成
"这分钟还没 bar"跳过 ⇒ 240 根重放完一单没提 ⇒ 意图 `queued` 挂到次日被
`expire_stale` 清掉——机会丢了且原因不可见。

- `mark_unavailable()`：会话开局/首根 bar 问一次标的表，不在的记
  `INSTRUMENT_UNAVAILABLE` + 中文原因；**问不到表就返回 `None` 并什么都不判**
  （不许靠猜测判死意图）。这里现网还照出我一处语义分裂：上一版写
  `if not known` 把 `None`（问不到）与 `set()`（问到了、全都不在）混成一件事，
  于是单只北交所意图的会话一条都不定局——空集合恰恰是本次要处理的那一幕。
- `resolve_outcomes` 新增判定：这种状态 ⇒ `rejected`（带原因），不是继续 `kept`。
- `check_session_gates(..., settled_biz=)`：会话自己已经定过局的单子不再被算成
  `IdleSpin…静默失败`——报警在已经解释清楚的事情上喊"静默失败"，喊几次就没人信它。

现网验证（库副本 + 真会话，12:0x）：

```
rq tick exit=0
920156_BUY_100@2026-11-03T11:20:00 | rejected
{"reason": "bundle 里没有 920156.BJ（北交所/未收录新股？）——新引擎买不了，规则可重新触发",
 "order_status": "INSTRUMENT_UNAVAILABLE", "session": "2026-11-03"}
```

## 3. 门禁与可复算性

- `tests/test_market_caliber_t59.py`（13 例，段表 + 五口一致性）
  + `tests/test_minute_supply_t59.py`（7 例，流水去重/心跳/闸显示口径）
  + `tests/test_m3_takeover.py` 追加 3 例（`mark_unavailable` 三种输入）。
- `spikes/mutation_t59.py`：**10 条变异 10 条捕获**（段表/分钟前缀/报价口/标的口/
  涨跌停带/raw_gate 截断显示/流水刷屏/定局退回空转/None-空集合混淆/IdleSpin 失真）。
- 全套 **672 passed / 9 skipped**。
- 分支 `worker/t59-t60-caliber`（**未合 main**）；NEC 现网工作树已指到该分支 tip，
  回退 `cd /home/nec/paper-trading && git checkout 1562c8a`。
- 账本未写：`paper_account.db` md5 与本任务开始前同一值
  （`cb624c9fcc3bf927006d31e4a442ba41`，11-03 上午 OpenCode 重置峰值后的新值）。

## 4. 留给 T60 的两条（已转过去，不在本报告里修）

1. `daily_bars_raw` 只有 11-02 之前的料 ⇒ 见 `report-t60-raw-gate.md`（采集时刻与
   源发布时点错配，我已把 `40 8 * * 1-5` 那次盘前采集装上）。
2. 北交所标的既然在 bundle 里根本没有，**池里该不该有它们**是拍板问题：
   现在会被当场 `rejected`（不再静默），但每天仍会重复"触发 → 拒"。
   要么装带 `.BJ` 的 bundle，要么在股票池/`universe` 侧过滤掉非沪深标的。
