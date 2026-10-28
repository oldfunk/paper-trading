# WORKER — 协作协议与任务箱（主 AI ↔ 员工 AI）

> 你是本仓库的员工 AI（打下手 + 找问题）。主 AI（我）负责交易代码的最终把关、
> 部署与发布。本文件通过 git 同步——**开工先 `git pull --ff-only`，完工推回去**。

## 协作协议

> **审查分工（2026-10-27 用户定）**：两位同事与我并行工作，**主要职责是
> 审核我的工作**，防止我走歪或丢重要细节——
> 同事 A（Qoder）：选型/spike/方案反驳审查（T5'、T6 报告作者）；
> 同事 B（Hermes）：现网 correctness 独立审查（H-1..H-8 报告作者）。
> **主 AI 的代码与决策推送后，须经至少一位同事复核并在本仓库留意见**
> （报告或 `questions.md`）；有异议先改再合，不许静默通过。

| 事项 | 约定 |
|---|---|
| 同步 | 开工前 `git pull --ff-only`；分阶段提交，不要攒大包 |
| 报告 | 写 `docs/worker/report-<任务号>.md`（如 `report-t1.md`），按 P0/P1/P2 分级，每条带 **文件:行号 + 复现/论证思路** |
| 状态 | 完成一项就把本文件对应复选框勾掉（`- [x]`），提交信息含任务号 |
| 文档/测试改动 | 可直接提交 main，前缀 `docs(worker):` / `test(worker):`；**禁止 force push、禁止删历史** |
| 产品代码改动 | **一律开分支** `worker/<任务号>`，push 分支后在报告里写明"待 review"，由我 review 合并；不要直接改 main 上的 `paper_trading/**` |
| 疑问/阻塞 | 写 `docs/worker/questions.md`（追加式），不要自己猜着改设计 |

## 红线（违反即事故）

1. **不跑任何写账本的命令**：`agent run/tick/fire`、`buy/sell`、`corporate apply`、
   面板 POST——只读 CLI（`status/nav/history/data coverage/data compare`）可以。
2. **不碰 Pi、不碰 secrets.local.json、不部署、不打 tag、不改 cron**。
3. 产品代码只提案不直改（见上表分支流程）；`config.yaml` 的默认值不许动。
4. 不接券商实盘、不做 tick/HFT/期权/做空（用户决策，勿扩展）。
5. 发现**账本错误/安全漏洞**级问题：立即写进报告标 P0 并 `questions.md` 通知，
   不要自己修，等我处理。

## 背景速读（开工前必读，按序）

0. `docs/founding-mandate.md` —— **立项约束偏差记录（必读第 1 位）**：本项目
   立项要求"用成熟开源量化项目搭建"，当前为自研，结论待用户拍板
1. `docs/incident-2026-10-27.md` —— 刚发生的交易事故复盘（尺度污染，已冲正）
2. `docs/TODO-code-review-20261027.md` —— R0–R9 全量审查清单与结论（**已知问题都在这**，你的价值是找它没覆盖的）
3. `AGENTS.md` —— 项目硬规则（数据不许编造、fail-closed、可审计……）
4. `docs/worker/report-defect-attribution.md` —— **代码问题的量化归因**（L1 重造面积 44.6%／钱类缺陷 83% 可被框架消灭／61% 缺陷在 L2 框架管不到）＋ §6 M1 验收靶子；L3 双审的分工按 §7

---

## 任务箱 · Batch 1（2026-10-27）

### T1 对抗性重审：执行链与账本（找我没找到的）

- [ ] 范围：`paper_trading/agent/loop.py`、`broker/paper_broker.py`、
  `risk/risk_manager.py`、`engine/ai_conditions.py`、`engine/conditional.py`
- [ ] 视角：带着事故复盘的"尺度/同源自洽/接线断层"三类模式，**再找别的类别的**
  （状态机漏洞、幂等缺口、竞态、异常路径吞错、边界数值）
- [ ] 产出 `docs/worker/report-t1.md`；**不改代码**
- 验收：每条发现可论证（贴代码链路），且确认不在 R0–R9 清单里

### T2 文档一致性清扫（可直接修文档）

- [ ] `README.md`、`docs/*.md` 里的命令示例/配置键/默认值 vs 代码实况逐条对照
- [ ] 过期内容直接改（前缀 `docs(worker):`），拿不准的进 `questions.md`
- 验收：抽查 3 条修改均与代码一致；报告列改动清单

### T3 测试覆盖盘点（可补纯测试）

- [ ] 盘点本周新增/改动大的模块的测试缺口（尤其 `integration/mother.py`、
  `cli.py` 新子命令、`agent/events.py`）
- [ ] 产出 `report-t3.md`（缺口表：模块/函数/风险等级）；能补的补
  （前缀 `test(worker):`，只加测试不改产品代码）
- 验收：`python -m pytest -q` 全绿（当前基线 **296 passed, 1 skipped**）

### T4 路线图待办可执行性评估（纯调研）

- [ ] 读 `docs/ROADMAP.md` §2–§4 未勾项，挑出"**不依赖母库、现在就能做**"的，
  写成带验收标准的任务草案（`report-t4.md`），标注预估改动面
- 验收：每条草案含"改哪些文件/怎么验收/依赖什么"

### T5' 迁移方案反驳审查 + 桌面 spike（P0，替代原 T5）

- [ ] 背景变化：用户已拍板"量化核心必须用成熟开源框架、零自研"；主 AI 已产出
  迁移方案 `docs/refactor-plan-open-source.md`（vnpy + rqalpha 双框架）
- [x] **反驳审查**：通读方案，专门找漏洞——技术假设错误、遗漏的能力点、
  风险低估、更该入选的框架；产出 `docs/worker/report-t5.md`，
  每条反驳要有依据（文档/代码/检索），**不许和稀泥**（→ `report-t5.md`，
  已促成 §7 拍板作废与 rqalpha 单引擎定案）
- [ ] **桌面 spike**（用户电脑，隔离 venv，不污染现有环境）：
  建 Python 3.13 venv（如 `C:\Users\yanzh\venvs\refactor-spike`），
  `pip install vnpy rqalpha`，跑两个 hello：① vnpy EventEngine+撮合引擎
  下单→成交→账户查询（无 GUI）；② rqalpha `run_app` 空策略回测
  （数据可先用其自带 fixture/示例）；记录版本号、坑、安装体积
- [ ] 红线：不碰 `~/paper-trading` 生产环境、不碰现有 venv、不改产品代码；
  spike 代码放 `spikes/`（新建目录，gitignore 可选）或报告内嵌
- 验收：反驳报告 + spike 实录（含 vnpy 4.5 在 3.14 vs 3.13 的安装差异确认）

---

### T6 akquant 挑战者 spike（P0，半天，决定终选翻不翻盘）

- [ ] 背景：选型主线已定 **rqalpha 单引擎**（`docs/refactor-plan-open-source.md` §7），
  但 akquant（MIT + AKFamily 同门 + Rust）是唯一可能翻盘的挑战者——
  用**两个否决性问题**验证，不是全量打分
- [x] 问题①**aarch64 可装性**：PyPI/官方是否提供 manylinux aarch64 轮子
  （Rust 内核没有轮子就要交叉编译 = 否决）；在 Pi 隔离 venv
  （`/home/pi/refactor-spike`，与你之前 spike 同一环境）实测 `pip install akquant`
- [x] 问题②**模拟撮合/T+1 能力**：源码取证——有没有常驻/模拟盘撮合（不是纯
  回测）、T+1 冻结语义（`tplusone`/`Position.available`）、涨跌停拒单、
  卖出印花税、100 股整手；每条给 文件:行 证据
- [x] 产出 `docs/worker/report-t6.md`：两问各有明确是/否 + 证据；
  结论格式「akquant 反超 / rqalpha 定案」二选一，不许含糊
- 验收：aarch64 实测结果 + 撮合能力源码取证；**不改产品代码、不碰生产环境**

**状态**：Batch 1 T1–T4 + T5' 已交付；**T6 已交付 → akquant 不反超（缺涨跌停带），rqalpha 定案**；主 AI 已完成 H 系修复（`caa7371`）与 M-1/M-2/M-3 三缺陷修复，全量 **316 passed**。
  注：T6 的两处未做项见 `report-t6.md` §3（Pi 真机安装由主 AI 一枪收口；akquant 策略执行验证约半天，不在否决路径上）。

### T7 rqalpha 数据源适配器取证与口径归口设计（Qoder，P0，M0b 关键路径）

- [ ] 背景：T6 判决 **rqalpha 定案**；M1 第一步 = `AbstractDataSource` 接我们的数据
- [ ] 在 spike venv（`C:\Users\yanzh\venvs\refactor-spike`，rqalpha 6.4.2 已装、
  bundle 已下）读源码，枚举 `AbstractDataSource` 必须实现的方法/属性（**文件:行**）
- [ ] 逐方法映射到我们的数据（SQLite `daily_bars` / bundle 元数据 / 交易日历）
- [ ] **口径归口设计（refactor-plan §7.3 红线，本任务核心）**：论证两条路线的
  除权日行为——路线 A「我们的 qfq 序列入 + 因子置中性 + bundle 只供停牌/ST/
  日历/分红元数据」 vs 路线 B「raw 价 + bundle 复权因子」；必须回答
  "**除权日净值/持仓会不会错**"，举一个真实分红案例逐步推演
- [ ] 产出 `docs/worker/report-t7.md`：接口表（文件:行）+ 数据流 + 难点清单
  （哪些方法我们给不了、退路是什么）+ 口径结论
- 验收：**不含产品代码**；口径结论有除权日推演

### T8 H 系修复复核（Hermes，P0，L3 审查）

- [ ] 复核 commit `caa7371`（H-1/H-2/H-4/H-6/H-7 修复 + H-3/H-5 锁定测试）：
  修复是否正确、测试是否真能红（mutation 视角）、**有无过度拦截**
  （正常下单/挂条件路径是否被误伤）
- [ ] 产出 `docs/worker/report-t8.md`：逐条 verdict（修复正确 / 需改 / 误伤）
- 验收：只审查不改码；发现 P0 立即 `questions.md` 喊停

**缺陷量化基线**（主 AI 已采纳）：`report-defect-attribution.md`——
L1 44.6% / 钱类 83% 框架可灭 / **L2 61% 是迁移后的主战场**（1.53 缺陷/KLOC）；
其 §6 的 M1 七条验收门禁已并入 `refactor-plan` §5.1。

---

## 当前状态与任务分配（主 AI 更新 2026-10-27 深夜 · 用户指示：主 AI 减负、多派活）

- [x] T8 已交付（`report-hermes-t8-20261027.md`：H-1/2/4/6/7 修复通过、
  M 系通过、H-5 仍开 → 主 AI 已裁决 **consume-once** 并修复
  （`test_plan_consumed_even_if_execution_crashes`），分歧记录见 `questions.md` 批复节）
- [x] **T7 已由主 AI 接管并完成**：`docs/m1-data-adapter.md`——store 接口取证
  （adjust.py 读时复权 / position_model 分红入账）+ 口径归口定案（回测用 bundle
  自洽三件套、实时线用我们两源+尺度闸）+ **M1-A 端到端跑通**
  （`spikes/rqalpha_ma_backtest.py`：trades=108、NaN 门禁实弹验证、EXIT=0）
- 全量测试 **317 passed**（本地+Pi 双绿，v0.7.10 已部署）

### 已派发任务（认领即勾选；主 AI 只做最终裁决/门禁签核，不亲自实施）

- [x] **T9（Qoder，P0，M1-B parity 实施级）已交付并合并**（`report-t9.md` +
  `spikes/parity_t9.py`）：三层归因实验，判定原容差结构性不可达 → 主 AI 已裁决
  **D1–D4 + 两级门禁**（`refactor-plan` §5.2/§5.3）
- [x] **T10（Qoder，P1）已交付并合并**（`report-t10.md` + `spikes/exdate_t10.py`）：
  route B 除权日验证通过；rqalpha 过户费缺失/分红无红利税实测证据
- [x] **T11（Hermes，P1）**：H-5 consume-once 裁决再复核（`questions.md` 批复节）；
  无异议，见 `report-t11.md`
- [x] **T12（Hermes，P0，与 T9 交叉）T15b1/T16 已交付**（见 Batch 3 勾选）；
  **T12 本体仍开且扩权**：
  - [ ] **收口 T9 坦白的 26 笔未归因缺口**（`report-t9.md` §3.6：候选=once 每日
    语义/涨跌停一字板拒单/T+1 冻结/多次触发——用 `spikes/parity_t9.py` 复跑归因）
  - [ ] **陈旧引擎审查移交的 11 条重定位**（Qoder 移交：符号/行号已失效，
    原始清单在 `C:\Users\yanzh\AppData\Local\Temp\qoder_engine_notes.md`，
    按当前 HEAD 逐条判"仍在/已修"，不许按旧行号改码）
  - 产出 `report-t12.md`

**主 AI 保留职责（不再亲自写实施）**：L1 交易代码最终把关（缺陷归因 §7）、
T9/T10 结果裁决与 M1 门禁签核（§5.1 不绿不合）、P0 事故响应、M2 方案拍板。

---

## Batch 3 · 挑战性长线任务（主 AI 派发 2026-10-27 深夜）

> 验收备注：T11 ✅ 已交付合并；**T9/T10/T12 无交付物，继续挂起**（口头完成不算，
> 推送到仓库才算）。Batch 3 与挂起项并行，认领即勾选。

### T13（Qoder，P0·M2 关键路径·长线，预计 2-3 天）rqalpha 模拟盘运行模式取证 + 原型

- 挑战点：回测的 `simulation_event_source` 是**窗口内回放**，而我们要的是
  **常驻模拟线**（Pi cron 世界：每日新 bar 灌入、T+1 跨日结算、断点续跑）。
  rqalpha 自述有"实盘模拟"——它到底怎么喂新鲜数据？事件源可否替换？
- 任务：
  - [ ] 取证 rqalpha 的 live/sim 运行路径（源码 `文件:行`：事件源注册、
    数据源热更新、日终结算触发），回答"持续运行一天模拟需要什么"
  - [ ] 原型：用我们 SQLite 的**历史某日**数据当"今日"，跑通一天模拟
    （下单→成交→日终结算→T+1 冻结），数据不缺任何字段
  - [ ] 明确断点续跑方案（Pi 进程每天重启，状态怎么恢复）
  - [ ] 产出 `docs/worker/report-t13.md`：可行性结论 + 原型代码（放 `spikes/`）
- 验收：原型可复现（命令+输出）；**不能跑通就写清卡点**，不许绕过

### T14（Qoder，P1·长线）研究流水线移植设计：grid/walkforward → rqalpha

- 挑战点：现 `engine/optimize.py`/`walkforward` 的纪律（网格只是筛选器、
  必须样本外、0 笔成交不参与排名、基准超额）要在 rqalpha 上原样复活，
  且叠加 **NaN 门禁 + 我们的 provenance 指纹** → 可复现实验注册表
- 任务：
  - [ ] 对照现实现逐语义映射（每条纪律 → rqalpha 对应物或缺口清单）
  - [ ] 设计实验注册表格式：bundle 版本 + 参数哈希 + 数据指纹 + 指标有限性证明
  - [ ] 产出 `docs/worker/report-t14.md`：映射表 + 缺口 + 实施步骤（先设计不写码）
- 验收：映射表每行有现代码 `文件:行` 出处

### T15（Hermes，P0·长线，预计 3-5 天）L2 对抗性测试战役——狩猎新缺陷

- 背景：缺陷归因 §7 说 L2 是主战场（**1.53 缺陷/KLOC**，`agent/loop.py`+
  `cli.py`+`integration/mother.py` 三文件占 L2 的 42%）。你的 H 系列已经证明
  能在全绿测试下找到真 bug——这次是**系统性狩猎**，不是抽查
- 任务（每周节奏，按批交付）：
  - [ ] 批次一：`agent/loop.py` 状态机对抗——`_plan`/`_execute_plan`/
    `_execute_actions` 的部分失败、动作交错（条件单与直接单同标的）、
    cap 边界（正好 n、n+1）、计划过期与消费竞态、dry-run 与实盘路径分叉点
  - [ ] 批次二：`integration/mother.py` 降级矩阵对抗——缓存与 schema 变更交错、
    尺度闸与绝对闸同时触发、local/mother 模式切换中途的 provenance 一致性
  - [ ] 批次三：`cli.py` 编排对抗——锁被抢/超时半途、`--json` 错误路径、
    子命令参数非法组合
  - [ ] 每批：新发现 → `report-t15-batchN.md`（H 系列格式：文件:行+复现+风险级）
    + 能补的回归测试直接 `test(worker):` 提交
- 验收：每批至少给出"查过什么、为什么这里没有 bug"的覆盖清单（阴性结果也算交付）

### T16（Hermes，P1）合规能力回归套件 `tests/test_compliance_regression.py`

- 背景：`refactor-plan` §5.1-6 要求"6 条钱类缺陷逐条写成回归测试，迁移前后
  各跑一次，同一份测试证明 L1 替换没把合规能力换丢"——这份套件就是**迁移的
  合同**，M1-B 验收要引用它
- 任务：
  - [ ] 把 6 条钱类（事故本体/价源绕过/条件无尺度闸/H-1 fail-open/H-4 退化/
    H-7 旁路）+ 10-27 事故日场景，各写成独立命名的用例
  - [ ] 每条标注溯源（对应 incident/报告章节），并设计"rqalpha 版对照位"
    （同一场景在新引擎上怎么验，留 TODO 占位）
  - [ ] 产出提交 `test(worker):` + 在报告里说明每条的通过语义
- 验收：全绿；迁移 M1-B 时此套件必须**双跑**（旧引擎+新引擎）

### Batch 3 追加（主 AI 裁决 D1–D4 后派发，2026-10-28）

- [x] **T15 批次一已交付**（`report-t15-batch1.md`：阴性结果+3 测试）→ 批次二/三继续
- [x] **T16 已交付**（`tests/test_compliance_regression.py` 166 行，全绿）
- [ ] **T17（Qoder，P0·小）**：修「金额不足一手 → 静默 skipped」（T9 §3.2，
  14 笔+连带 15 笔，违反纪律 4）——`engine/backtest.py` 的 skipped 已有归因，
  缺的是**报告化**：`metrics` 显式输出 skip 分类计数 + 面板/CLI 可见；
  补一条"必须非静默"的回归测试；`fix(worker):` 提交
- [ ] **T18（Qoder，P0·D1 关键实验）**：分钟频率可行性——bundle 是否含 1m
  数据（`stocks.h5` 外的 minute 文件/配置）；rqalpha 用分钟频率能否实现
  **T+1 开盘撮合**（信号 T 收盘 → T+1 首分钟成交）；出结论：
  可行（回测线口径统一路径）/不可行（M2 模拟线为唯一 T+1 路径）→ `report-t18.md`
- [ ] **T19（Qoder，P0·S 级门禁实施）**：按 `refactor-plan` §5.3 实现 S 级
  信号集合对照——基于 `spikes/parity_t9.py` 改造：同价源同锚（route B）、
  股数语义、±1 交易日匹配、匹配率统计 + 未匹配逐笔归因表；
  输出 `report-t19.md`（M1 签字必备件）

### 生产迁移 NEC（主 AI 派发 2026-10-28，Pi 过劳实锤：load 4.4 / free 59MB / 连续自重启）

- [x] **T20（Qoder，P0·迁移 Phase 0 环境搭建）已实证验收**（主 AI 直测）：
  NEC venv Py3.12.3、**326 passed, 1 skipped** 与基线一致、repo 就绪；
  报告缺失不阻塞（实测即验收）
- [x] **T21（Hermes，P0·迁移 runbook 审查）已交付并采纳**（`report-t21.md`）：
  P0 回退顺序错误已修入 runbook；P1（sha256/扩展表/local 配置/凭证提醒）全部落地
- [x] **生产迁移已完成（2026-10-28 14:47，主 AI 亲执行，用户指示提前切换）**：
  Pi 静默（0 进程 0 cron）、NEC 生产运行（engine cron 分钟级正常触发、
  风控熔断态正确传递、dashboard systemd 200）；数据 sha256 逐字节一致；
  执行记录见 `migration-nec.md`；**Pi 冻结为回退副本，观察期 2-3 交易日**

---

## Batch 4 验收（主 AI 2026-10-28 晚）

- [x] **T24（Hermes）已交付并采纳**（`report-t24.md`）：**P0 成立**——SIGKILL/
  SIGTERM 不执行 finally，消费滞留 finally 时计划残留 pending → 下 tick 重跑
  已成交动作。**主 AI 已修**（commit `fb6cfcc`）：消费前移到 `_execute_plan`
  第一句（顺带堵掉解析段在 try 外抛异常同样残留的漏洞）；`finally` 删除；
  mutation 验证——消费挪回旧位后**两个测试真红**。
  **对 Hermes 建议的裁决**：其"running 状态 + SIGTERM handler"两方案均**不需要**
  （消费前移后被杀时计划早已消费，状态机与信号处理皆无必要——更简单且覆盖全信号）。
- [x] **T23（Hermes）实现验收通过，测试欠账**：`data coverage --strict` +
  白名单 + diff 脚本 + 非 strict 行为不变；**但 0 新增测试**（326→326 未动）。
  - [ ] **T23b（Hermes，P0·补测试）**：strict 降级→exit 1 / 白名单放行→exit 0 /
    非 strict 不受影响 三条用例；白名单文件当前为空是正确状态（母库迁移期
    strict 必红=符合红线），测试需以 tmp 白名单文件构造放行场景
- [ ] **T12 扩权 / T15b2/b3（Hermes）继续**（M1 四条件之"26 笔缺口"在其内）
- [ ] **T13/T14/T17/T18/T19/T22/T25（Qoder）继续**（M1 三条件 T17/T18/T19 打头）

### Batch 4 任务定义（历史，主 AI 派发 2026-10-28 下午）

> 背景：生产已迁 NEC；母库对照发现 **40 键降级**——**用户裁决（2026-10-28）：
> 母项目（stock-dashboard）正在自行迁移，子项目一律不介入母库**（不回填、
> 不同步、不诊断母库内容）。子项目侧事实：`daily_source=auto` 已自动回退
> local:akshare，交易链无中断；母库迁移完成后富化接口自动恢复。

- [ ] **T22（Qoder，P0·运维 fail-loud）NEC 生产心跳监控**：
  Pi 连续自重启无告警、靠用户肉眼发现——这类"静默故障"必须消灭。设计并实现：
  - [ ] 心跳件：定期记录 engine/agent 最后成功轮次（如写 `var/heartbeat.json`）
  - [ ] 看门狗 cron：每 10 分钟检查心跳年龄 + dashboard systemd 活性，
    超阈值（engine >5min 未跳动）→ 面板显著告警 + `op_log[data:health]`；
    **不引入外网依赖**（纯本地/局域网）
  - [ ] 恢复检测：故障恢复后告警自动消除，不留陈旧红条
  - [ ] 测试 + `report-t22.md`：注入停滞的手工验证步骤
- [ ] **T23（Hermes，P0·数据完整性门禁）coverage 严格模式**：
  主 AI 手工对照 Pi vs NEC `data coverage` 71 键发现 **40 键降级**（母项目
  迁移期状态，用户裁决不介入）。子项目要做的是把"数据不缺"变成**可重复
  验证的门禁**：① 对照脚本（两机/两时点 coverage 输出逐键 diff，**降级白
  必须显式列举并注明归属母库**）② `report-t23.md` 列 40 键与降级原因归属
  ③ `data coverage --strict` 模式或独立测试：白名单外的任何降级=失败
  （用户红线：不能缺任何数据）；**只做子项目 CLI 与测试，不碰母库、
  不催母库修复**（母项目自行迁移中）
- [ ] **T24（Hermes，P1·挑战）L2 崩溃恢复对抗——信号语义专项**：
  NEC 是 systemd/cron 世界，进程随时可能被 SIGKILL（升级/断电/OOM）。
  对抗审查点（H 系列风格：文件:行 + 复现 + 风险级）：
  - [ ] `agent/loop.py` 计划执行在 **SIGKILL/SIGTERM/断电**下的状态一致性
    （注意：`finally` 在 SIGKILL 下**不会执行**——与 H-5 consume-once 裁决
    交叉验证：哪些信号下计划残留 pending？下 tick 重跑的实际后果？）
  - [ ] `engine --once` 中途被杀：锁残留接管语义（AGENTS 称 PID 标识可自动
    接管——在真 SIGKILL 下验证）、账本写一半（SQLite 事务边界）
  - [ ] cron 轮次重叠（上轮未完下轮启动）时 `run_lock` 实际行为
  - [ ] 产出 `report-t24.md`；P0 立即 `questions.md` 喊停
- [ ] **T25（Qoder，P2·小活）部署文档同步 NEC**：AGENTS.md「Pi Deployment
  Sync」一节 + Cron 示例路径 `/home/pi` → `/home/nec`（生产真相已变）；
  以 `docs/migration-nec.md` 为新真相源，AGENTS 只改指引不复制内容
