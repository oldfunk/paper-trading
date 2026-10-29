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
| 交付前自检（2026-10-29 新增，Qoder 裁决 4 漏落地教训） | 交付 checklist 必须含**「对照 questions.md 批复 + refactor-plan 裁决逐条核销」**——每条裁决标注"已落地于 XX 文件/测试"或"不适用+理由"；**禁止拿自己的实现反解拍板**（实现与裁决冲突时以裁决为准，冲突本身要上报） |
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
  - [x] 取证 rqalpha 的 live/sim 运行路径（源码 `文件:行`：事件源注册、
    数据源热更新、日终结算触发），回答"持续运行一天模拟需要什么"
  - [x] 原型：用我们 SQLite 的**历史某日**数据当"今日"，跑通一天模拟
    （下单→成交→日终结算→T+1 冻结），数据不缺任何字段
  - [x] 明确断点续跑方案（Pi 进程每天重启，状态怎么恢复）
  - [x] 产出 `docs/worker/report-t13.md`：可行性结论 + 原型代码（放 `spikes/`）
- 验收：原型可复现（命令+输出）；**不能跑通就写清卡点**，不许绕过
- **交付（Qoder 2026-10-28，`worker/t13` 分支，待 review 不合并）**：
  用**生产 data.db 真实日线**跑通模拟日 + 跨进程续跑，**21/21 门禁全绿**
  （`spikes/live_sim_t13.py selftest`）。**M2 三个 P0 卡点**：① 我们的
  `daily_bars` 是**前复权**，rqalpha 契约要原始价（84.3% 标量-日不符，最坏
  低估 67.6%；再乘 bundle 因子＝双重计入，D3 事故家族）；② **6.4.2 core 里没有
  persist provider 实现**（`interface.py:643` 只有抽象类，CLI 无 `-p`），断点续跑
  必须我们自带；③ bundle 之后的新鲜日**原始价无法自证**（7/38 标的因子外推，
  最大 1.0459；且 2026-10-28 只有 29/38 有 bar）。另有 5 条常驻线特有的 P1
  （跨进程重复结算打爆 sys_analyser、同日重跑重复成交、band 缺数据 fail-open、
  前端拒单不落 Order、进程内重入被日志掩盖真因）。详见 report-t13 §4/§5/§6。

### T14（Qoder，P1·长线）研究流水线移植设计：grid/walkforward → rqalpha

- 挑战点：现 `engine/optimize.py`/`walkforward` 的纪律（网格只是筛选器、
  必须样本外、0 笔成交不参与排名、基准超额）要在 rqalpha 上原样复活，
  且叠加 **NaN 门禁 + 我们的 provenance 指纹** → 可复现实验注册表
- 任务：
  - [x] 对照现实现逐语义映射（每条纪律 → rqalpha 对应物或缺口清单）——
    `report-t14.md` §2 八条纪律逐行给现代码 `文件:行` + rqalpha `文件:行`
  - [x] 设计实验注册表格式：bundle 版本 + 参数哈希 + 数据指纹 + 指标有限性证明
    —— §4 `experiments` 表 + 状态机（`ok/excluded_zero_trade/invalid_nan/failed`）
    + `exp_id = hash(bars|params|days|engine_ver)` 的可复现性判据
  - [x] 产出 `docs/worker/report-t14.md`：映射表 + 缺口 + 实施步骤（先设计不写码）
- 验收：映射表每行有现代码 `文件:行` 出处 ✅（§7 出处自检）
- **交付要点（Qoder 2026-10-28，`worker/t14` 纯文档）**：
  ① **rqalpha 6.4.2 core 没有任何寻优设施**（全库 grep `optimize` 零命中，
  只有 `utils/concurrent.py:40` 一个带进度的进程池）⇒ 网格/排名/0 笔剔除/样本外切分/
  基准超额**五件纪律全是我们 L2 的活**，移植只把"评估一个变体"换成一次 rqalpha 会话；
  ② 基准建议**自算等权买入持有**（用同一段 rqalpha 会话跑 buy-and-hold），
  因为 analyser 的 benchmark 是指数口径且在 bundle 覆盖不足时**直接抛错**
  （`mod.py:203`，T13 §3 S1 现场撞过）；
  ③ **一 trial 一进程**（G-T13-9），否则被 `Environment` 单例清空后的日志掩盖真因；
  ④ 顺带发现 **P1 缺陷**：`walk_forward()` 承诺的过拟合量度其实没算
  （`optimize.py:251-252` 的 `oracle` 与 `picked` 是同一行、`picked` 全文未被使用），
  修法见 §6，建议与实施步骤 S3 合并做。

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
  **报告已补交**（`report-t20.md`）：3.12 免装 3.13、零 cron、面板 200；
  **P0 发现：NEC 是 x86-64-v1 CPU（Core2 Duo T9900）**——numpy≥2.4 wheel
  导入失败、未 pin 版本的 fresh install 会坏（现生产环境因已装好不受影响，
  但新机部署必须 pin；rqalpha 实验环境若上 NEC 也受此约束）→ 已转 T30 处置
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

### Batch 4 分支验收（主 AI 2026-10-28 晚 · Qoder 四分支已全部合入 `9f88f08`，340 passed）

- [x] **T17 ✅**：skip_counts/order_attempts/signal_drop_pct 进 metrics + 面板
  跳过分类展示 + 3 回归测试（变异可杀）——纪律 4「不静默」落地
- [x] **T18 ✅（D1 判决）**：机制可行（`next_bar` 仅 1d 降级）数据不可行
  （bundle 零分钟）⇒ **D1 终局裁决已落 `refactor-plan` §5.3**：回测线接受
  日线 current_bar + F 级书面容差；T+1 正式路径 = M2 模拟线；新增 T26 积累线
- [x] **T19 ✅（M1 关键件）**：S 级达标 **97.3%(raw)/100%(同锚)**、判定层
  **111/111 全等**、26 笔缺口收口为 **29 笔归因**（14 一手+15 连带）；
  自曝 harness numpy bug（诚实加分）；M1 四条件**三达成一待**（T16 双跑）
- [x] **T22 ✅**：heartbeat.py + 看门狗 cron + 面板红条（恢复自动消失）+
  `/api/health`；代码审查过（心跳 try/except 不拖垮决策链）
- [x] **T25 ✅**：AGENTS cron 路径 → NEC + 3 条实测环境坑（x86-64-v1/numpy、
  XDG_RUNTIME_DIR、ufw）

**新派发**：

### Batch 6 · M1 收尾冲刺（主 AI 派发 2026-10-29 · 目标：凑齐签字条件后 M1 关闭）

> M1 签字四条件现状：S 级 ✅（T19）/ 26 笔缺口 ✅ 基本（剩 000333 对）/
> D1 ✅（T18+终局裁决）/ **T16 双跑 ⏳（旧引擎绿，新引擎侧未做 = 本批核心）**。
> 签字文档框架已由主 AI 落档 `docs/m1-signoff.md`，两件交付回来即填终评。

- [ ] **T29（Qoder，P0·M1 签字最后拼图）T16 双跑——新引擎侧**：
  读 `tests/test_compliance_regression.py` 中 6 条钱类 + 事故日场景的
  **"rqalpha 版对照位 TODO"**，在 spike harness（`refactor-spike` venv +
  bundle，复用 `spikes/parity_t9.py` 骨架）实现新引擎侧对照：
  - [ ] **适用场景逐条实现**：如事故日尺度回归（10-27 场景在 rqalpha 数据上
    重放 → 期望同样拒单/无成交）、费用口径对照（佣金地板/印花税/过户费——
    过户费需 D4 decider，未实现前差值要量化）、涨跌停带拒单（rqalpha
    bar_matcher 内置行为实测）
  - [ ] **不适用场景逐条论证 N/A**：L2 门禁类（H-1 无基准拒单、H-4 条件
    缺现价）住 L2 不随 L1 替换——标 N/A **必须写清"L2 测试覆盖 + L2 在
    M2/M3 不动"的依据**，不许一句带过
  - [ ] 产出 `report-t29.md`：双跑结果表（场景 × 旧引擎结果 × 新引擎结果 ×
    等价判定 × 证据），**全表没有"未测"格**
  - [ ] 验收：跑得起来的都跑了；harness 必须带 `handler_invocations>0` 类
    空转断言（T19 踩过的 numpy 静默坑）

### Batch 5 · 项目内部梳理（用户要求 2026-10-29：验证接入 + 梳理结构 + 查文档漏洞）

- [ ] **T27（Hermes，P0·结构文档漏洞审查）**：文档之间与"文档 vs 代码"的
  一致性系统审查——产出 `report-t27.md`（H 系风格逐条：位置 + 现状 + 应为
  + 风险级）。范围：
  - [ ] **文档互查**：`AGENTS.md` / `refactor-plan-open-source.md` / `WORKER.md` /
    `mother-interface.md` / `incident-2026-10-27.md` / `founding-mandate.md` /
    `migration-nec.md` / `m1-data-adapter.md` ——同一事实两处陈述是否矛盾、
    是否有过时断言（例：D1-D4 裁决后仍写"等待分钟数据"的旧句子；T25 改 AGENTS
    后与 migration-nec 是否重复维护）、复选框状态与真实交付是否相符
  - [ ] **代码 vs 文档**：AGENTS 硬规则（0-6）/ 纪律 3.2/5/8 各条引用的
    `文件:行` 或行为描述当前是否仍成立；合规表 16 条"已建模/简化"状态抽查
    （每类至少 1 条实证）；Risk Parameters 表 vs `config.yaml` 实际默认值
  - [ ] **分层纪律落实度**：硬规则 0 的 L1/L2 划分——列出哪些模块的代码注释/
    文档声称属 L2 但实际在算 L1 的活（影响成交价/持仓/盈亏/拒单的数值来源
    逐个点名：现在 L1=自研 Backtester，rqalpha 未接生产——**如实记录过渡态**）
  - [ ] P0 矛盾 → `questions.md` 喊停；P1/P2 进报告清单
- [ ] **T28（Qoder，P0·结构地图 + 分层现状）**：产出 `docs/architecture-map.md`
  ——项目当前的"内部梳理"权威地图：
  - [ ] **模块地图**：`paper_trading/` 全模块清单（包/文件/行数/一句话职责），
    按 `engine / agent / data / integration / dashboard / cli / utils` 分组
  - [ ] **依赖方向图**：核心链路（cron → cli → engine/agent → broker → db）
    与数据链路（akshare/母库 → source → 价源消费者）的文字版数据流，
    标出**单向依赖违规**（谁反向 import 了不该 import 的层）
  - [ ] **L1/L2 现状 vs 目标**：对照 `refactor-plan` §1 三层——当前每模块的
    实际归属、M2/M3 后的目标归属、**迁移状态一览**（哪些已换/待换/永不换）
  - [ ] **重复与死区**：明显重复实现（如多处各自的取价/格式化）、
    从未被引用的模块（import 图验证）、测试盲区（无测试的生产模块清单）
  - [ ] 验收：行数/清单用工具输出（cloc 或脚本统计）不许估；依赖图从
    import 语句实测不凭记忆

- [x] **T26（Qoder，P1·长线·D1 闭合）每日 5 分钟线积累**（`worker/t26`，**待 review**；
  分支链 `t31 → t32 → t26`）——先设计后实施：真机实测 48 根/标的/日幂等落库、
  过期日只报缺口不假装、周期分道、保留窗口可关；新增 10 测，全套 397 passed 4 skipped。
  **顺带挖出一条真实数据损坏风险**：`minute_bars` 主键是 `(symbol, ts)` **不含 period**
  ⇒ 1 分钟与 5 分钟在同一时刻会互相覆盖（`ON CONFLICT` 静默改写）。根治要重建表，
  属钱级数据层改动，我**只报告不动手**，等你拍板（`report-t26.md` §5-①）。
- [ ] （原文留档）每日 5 分钟线积累：从现在起每日收盘后
  把池内（或全市场按需）5 分钟线落 `data.db minute_bars`（现仅 2304 根/约
  22 天）——设计：数据源与频率确认、每日 cron 或并入既有 tick、增量 upsert
  幂等、量级估算（保留窗口策略防 SD/磁盘涨满）、缺口补拉；攒够回测窗口后
  回测线可升分钟频率（D1 彻底闭合）→ `report-t26.md`（先设计后实施）
- [ ] **T12 收尾（Hermes）**：000333 一对（rq 07-03 / 旧 07-09）用 Qoder 存盘的
  `C:\Users\yanzh\AppData\Local\Temp\t19_capture.json` 逐日快照比对归因
  （111 条事件 × 21 根收盘，无需重跑）→ 并入 `report-t12.md` 增补节

### M1 关闭后追加（主 AI 2026-10-29 · M1 签字关闭，见 `docs/m1-signoff.md`）

### M2 拍板后实施批（主 AI 2026-10-29 · `refactor-plan` §5.4 已拍板，M2 启动）

- [x] **T13 ✅ 已验收合并**（`report-t13.md` + 原型两件）：21/21 门禁真重启实测、
  3 P0/5 P1 清单、断点续跑方案、9 条门禁建议——**M2 拍板全部采纳**
  （§5.4：close_raw 供给 / provider 钱类件 / 双保险新鲜度 / vwap→只用 current_bar /
  band 供给责任=我方）
- [x] **T31（Qoder，P0·M2 数据侧）close_raw 供给管道**（`worker/t31` 分支，**待 review**）：
  - [x] 读取链：~~母库 `close_raw` 优先~~ → **实测修正**：母库只有
    `close_raw/volume/amount` 是不复权，`open/high/low/close` 四列本身就是**前复权**
    （600519 2016-01-04：母库 close 166.544=close_qfq，close_raw 210.02=bundle 原始价）
    ⇒ 改为 **akshare `adjust=""` 供 raw OHLC+成交额**，母库 `close_raw` 当**独立同日对照源**
    （喂料≠验料）；落库走**独立表 `daily_bars_raw`**，既有 qfq 数值一字未动
    （`daily_bars` 只加 `adjust='qfq'` 标签列，迁移用例断言过）
  - [x] **口径标签机制**：`Bar.adjust`；`upsert_raw_bars` 收到非 raw 直接拒写；
    `BarSource.engine_raw_bars()` = 引擎唯一取数口，口径不符/缺表/缺数/陈旧一律返回 `[]`
    并把原因留在 `last_engine_reject`（G-T13-1 fail-closed）
  - [x] G-T13-2 新鲜度：本地半边 `raw_freshness()`（按交易日历算滞后）+ 回测线决策函数
    `factor_freshness_ok(today, factor_last)`（bundle 因子末日读取由 T32 适配器做，产品不引 h5py）
  - [x] T26 同步：`minute_bars` 加 `adjust='raw'` 标签，实测印证最后一根 5m 收盘
    = 当日 raw 日收（000792 22.10 / 600519 1330.75）
  - [x] 测试 + `report-t31.md`：**新增 17 条**；全套 **357 passed, 1 skipped**
    （基线 `e28def3` 同环境实测 340 passed ⇒ 零回归）；CLI `data fetch-raw` /
    `data raw-coverage --gate`；拒收写 `op_log[data:raw_reject]`
  - ⚠️ 顺带发现：`mother.py:406-429` 把 `close` 当"不复权"兜底列是**误判**（它等于 qfq）——
    我没顺手改，已进 `questions.md`
- [x] **T32（Qoder，P0·M2 适配器产品化）T13 原型 → 产品件**（`worker/t32` 分支，**待 review**；
  依赖同为未 review 的 `worker/t31`，两条一起看）：
  - [x] 三件套进产品 —— 包名定为 **`paper_trading/integration/rq_engine/`**
    （**不能叫 `rqalpha/`**：曾把该目录注入 `sys.path`，结果遮蔽真框架包，见 report-t32 §5-④）：
    `RawDayBarStore`（raw + prev_close + band 三字段必供，缺带/OHLC 不自洽即
    `BandMissingError` 拒供）、`PaperDataSource`（bundle 元数据 + 热替换 CS store +
    `available_data_range` 覆盖 + `bundle_price_last/bundle_factor_last`）、
    **`FilePersistProvider` 按钱类件对待**（原子换名 + **fsync** + sha256 sidecar，
    校验和不符即拒绝恢复并把错误留在 provider——rqalpha 会吞这个异常）；
    **重启 cash/qty/closable/frozen 逐字段相等已在真实数据上实测**（E2→E3：
    昨日 100 股 `closable=0` → 今日 `closable=100`，现金 97,774.00 续上）
  - [x] G-T13-3/5/6/7/8/9 全部落地并逐项实测：band 缺失拒跑（`store`）、
    幂等会话闸 + 配置指纹 fail-closed（`provider.SessionGate`，E4 `rc=4`）、
    禁 `sys_analyser`（`build_config` 写死 + SETTLEMENT 重放降级为警告，E3）、
    空转断言（`check_session_gates`，就是它抓出 §5-①/④ 的真因）、
    `ORDER_CREATION_REJECT` 原因采集（E2 中文文案进 `rejects`）、
    一天一进程（`_SESSION_USED` 拒绝进程内重入）
  - [x] **双轨影子 runner**：`shadow.shadow_day()` 同日跑 rqalpha + `PaperBroker`
    （两侧**显式对齐** `initial_cash`/零滑点/同一 6 位代码键，否则比的是参数），
    `parity_diff` 逐字段对账：**持仓/T+1 可卖/成交笔数完全一致，
    `cash_delta=0.0222` = 过户费 0.001%×2221**（与 T29 同源）；
    写 `op_log[parity:diff]` 需 `--allow-op-log` 显式开启（默认不写生产账本），
    影子路径与生产 `paper_account.db` 同路径时直接 `RuntimeError`
  - [x] 测试 + `report-t32.md`：新增 **33 条**（30 条不需要 rqalpha，NEC 也能跑）；
    端到端 **16/16 PASS**（`spikes/e2e_t32.py`，生产真实原始价 + 母库验料，每项一个子进程）；
    全套 **387 passed, 4 skipped**（基线 340+1 ⇒ 零回归）；T13 的 spikes 原型保留为对照件
  - ⚠️ 顺带交付：CLI `rq day / rq shadow-day / rq state`；未装 rqalpha 时明确 exit 2 说明原因；
    `describe_error()` 剥 rqalpha 的 `ExceptionGroup`（mod 装载失败原本只露 `KeyError('ptpaper')`）
- [ ] **T33（Hermes，P0·M2 审查）拍板与原型双重审查**：
  - [ ] 审 §5.4 拍板：9 条 G-T13 门禁判据**是否可测**（每条给出测试方案草案
    或指出不可测）；close_raw 方案有无第三条 P0（如母库 close_raw 新鲜度/
    与 qfq 的一致性校验缺位）
  - [ ] 审 T13 原型代码（钱类视角）：FilePersistProvider 的原子写/指纹/
    恢复语义找漏洞；SqliteDayBarStore 的 band 计算与 AGENTS #6/#7 合规表
    逐档核对（10/20/30%、ST、四舍五入）
  - [ ] 产出 `report-t33.md`；P0 → `questions.md` 喊停（M2 动工前必须清零）

- [x] **T30（Qoder，P0·部署安全）requirements 版本 pin**（`worker/t30` 分支，**待 review**）
  - ① 现役盘点：NEC `venv/bin/pip list` → Python 3.12.3 / numpy **2.2.6** / pandas 3.1.0 /
    akshare 1.20.1 / pytest 9.2.0（现网"看起来没事"只因包早已装好，上限缺失只在重装/新机暴露）
  - ② 上限：`numpy>=1.26,<2.4`（**唯一有实测失败证据的一条**；下限同步抬到 1.26 以匹配
    pandas 3.x 实际要求）+ `akshare<2.0`、`pandas<4.0` 两条**护栏**（可驳）；
    `pyyaml/pytest` **不设上限**（无失败证据，加天花板只会添解冲突成本）
  - ③ **NEC fresh venv 实测**（临时 venv + 临时 clone，用完即删，不碰生产 venv）：
    解析到 `numpy 2.3.5 / pandas 3.1.0 / akshare 1.20.2 / pytest 9.2.0`，
    `pip check` 干净，**340 passed, 1 skipped** 与基线逐条一致；
    **反例**：同一台机 `pip install numpy pandas`（不 pin）→
    `RuntimeError: NumPy was built with baseline optimizations: (X86_V2)`
    ——装得上、跑不了，本轮现场复现。
    本地这一腿给的是**较轻证据**（如实标注）：`pip --dry-run` 解析出同一组版本
    （`numpy-2.3.5/akshare-1.20.2/pytest-9.2.0`）+ 现役 Py3.14 环境 **340 passed, 1 skipped**；
    本地完整 fresh venv 因 `lxml/mini-racer` 安装卡十分钟被我终止——
    架构相关的决定证据是 NEC 那台 v1 机器（见 report-t30 §3）
  - ④ 兼容矩阵 + 上限的代价（长期停 2.3.x / 换 v2+ 机器 / 源码编译三选一）
    + M2 装 rqalpha 时 h5py 等二进制依赖需重测 v1 —— 见 `report-t30.md` §4/§5

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

---

## 主 AI 终审（2026-10-29）：五分支按序合并完毕，"待 review"状态全部关闭

- **合并顺序**：`t31 → t32 → t26`（堆叠分支按序），`t30`、`t14` 独立——全部干净合入，
  全量 **397 passed, 4 skipped**（基线一致零回归）；T33 报告 + ST band P0 回执随 t32 进主线。
- **T14 ✅ / T26 ✅ / T30 ✅ / T31 ✅ / T32 ✅ / T33 ✅ 全部验收**。
- 全部拍板见 **`refactor-plan` §5.5** + `questions.md` 批复节（母库 OHLC 事实修正、
  A+B 当日防线、影子 op_log 默认写、过户费显式差+decider 排期、minute 主键重建、
  walk_forward 缺陷修、numpy 上限 (a)+lock 采纳）。
- **M2 动工 P0 已清零**（T33 确认）；生产 NEC 同步最新主线。

### Batch 7 · M2 收尾与拍板落地（Qoder 4 件 + Hermes 继续在途）

- [x] **T34（Qoder，P0·数据侧修正批次）已验收**（`worker/t34`，§5.5 裁决 1+2）：
  - [x] ① `mother.py` 注释如实 + 标签改 `"qfq"` + 参数化测试锁死
    （**事实校正**：生产母库 `close_qfq` 的 NULL 行数是 **0**——NULL 的是
    `close_raw/close_adj` 各 21 行 ⇒ 原 `adj="raw"` 分支当前不可达，属"母库
    回填一变就错"的潜在缺陷；按裁决仍改了，并新增 `mother.raw_close_asof()`）
  - [x] ② 当日跨源对照：`raw_feed.intraday_reference()`（腾讯实时，与 akshare
    不同源）+ `check_raw_bars(..., intraday, day, enforce_intraday)`：收盘后才
    强制、同日才比、偏差 >0.5% **整只拒收**；顺带修**时区坑**（`session_closed()`
    原用机器本地钟，生产机非 +08 会静默不对照，现统一折算北京时间）
  - [x] ③ G-T13-1 增补判据落成 `raw_flip_watch()` 三态：`waiting_reference`
    （只告警）/ `checked_not_flipped`（**停线**）/ `raw_stale|no_raw_data`（停线）；
    `data raw-coverage --gate` 并入
  - [x] ④ NEC 真机实测：`intraday_no_today_bar=3`——**15:09 新浪当天日 K 未出**
    对照空跑 ⇒ 新增显式计数，**采集时刻建议 ≥15:30（cron 由主 AI 定）**
  - +21 测，全套 **418 passed**；`report-t34.md`
- [x] **T35（Qoder，P0·钱类数据层）已验收**（`worker/t35`，§5.5 裁决 6）：
  - [x] `(symbol, ts)` → **`(symbol, ts, period)`** 在线迁移：判需要 → 备份
    `<db>.pre-t35.bak` → 建新表搬运 → 行数核对不等即回滚 → drop/rename/索引；幂等
  - [x] **生产真实副本演练**：2,304→2,304 行、备份可回滚、生产库未触碰；
    现网分钟仅 3 只（000858/002192/600036 各 768 行）
  - [x] 读写侧同步：upsert 冲突含 period；`runner._today_minutes` 与
    `backtest.load_minutes` 显式 `period="5"`
  - [x] 覆盖风险回归（同刻两周期并存互不覆盖）+ 6 配套 = +7 测，**404 passed**；
    `report-t35.md`；⚠️ 迁移触发方式待批（见 §5.6 批复）
- [x] **T36（Qoder，P1·缺陷修复）walk_forward oracle/picked**（`worker/t36`，**待 review**）
  - 修法：重排**之前**按样本内顺序取 `in_best=oos_rows[0]`、`oracle=max(oos_rows, key=oos)`；
    新增 `in_best_oos_excess_pct/_symbol/_params`、`oracle_oos_params`、
    **`overfit_gap_pct = oracle − in_best`**；死变量 `picked` 删除；
    旧键（`sample_in_best_excess/oracle_oos_excess/…`）**全留**保证面板不破；面板加一行
    "样本内第 1 名到样本外还剩 X%／事后最优 Y%／过拟合差距 Z%"（差距>0 标红）
  - 6 条回归 + **变异验证**：把代码临时改回旧形状 ⇒ 3 条必红；换回修复 ⇒ 6 passed
  - ⚠️ 一个我踩到并写进注释的要点：**不能用 `max(rows, key=in_excess)` 找"样本内第 1 名"**
    ——同分候选会挑到不同行（实测两组 `in_excess=-27.85` 并列）；权威定义是
    `in_sample_top[0]`，测试据此反查
  - 全套 **403 passed, 4 skipped**（基线 397+1，零回归）；产出 `report-t36.md`
  - 顺带记两条 P2（未动）：同分候选排序不保证可复现；样本外只检验 top-K 的取舍未量化

- [x] **T37（Qoder，P1·部署）lock 文件 + rqalpha 二进制 spike**（`worker/t37`，**待 review**）
  - ① `requirements.lock.txt`（NEC Py3.12.3 生产 venv `pip freeze`，35 包，含生成机/日期/用途
    与"不要放开 numpy、不要与引擎 venv 合并"两条警告）；`requirements.txt` 只加两行指针注释
  - ② spike（NEC `/tmp` 临时 venv，跑完 `rm -rf` + 清 pip cache，**生产 venv 零改动**）：
    **直接 `pip install rqalpha==6.4.2` 会解析到 numpy 2.5.4 → import 当场
    `RuntimeError ... (X86_V2)`**（第三次现场复现）；正确姿势是**先 `pip install 'numpy<2.4'`
    再装 rqalpha**，之后 numpy 2.3.5 + h5py 3.16.0 / scipy 1.18.1 / matplotlib 3.11.2(Agg)
    / statsmodels 0.15.0 / jsonpickle / logbook / pandas 3.1.0 / rqalpha 6.4.2 **全部 import 通过**，
    `run_func / BaseDataSource / SimulationMod / AbstractPersistProvider` 四入口可取，
    h5py 写读往返 OK，`rqalpha --help` 正常；venv 697 MB、安装 2m10s
  - 结论：引擎必须走**独立 venv**（`requirements-rqalpha.lock.txt` 已给），
    不与交易/面板进程混装；bundle 3.3 GB 下载未做（属部署决定，等主 AI 定放置位置）
- [ ] **Hermes 在途继续**：T15b2/b3、T27 ✅（已交付验收：P0 无阻塞）、T28

### Batch 8 · 影子期启动件（主 AI 派发 2026-10-29，批复见 questions.md）

- [x] **T38（Qoder，P0·裁决 4+5 联动）影子留痕 + 过户费 decider**（`worker/t38`，**待 review**）
  - [x] `--allow-op-log` → **默认写生产账本**：`cli.py:1362` 换成 `--no-op-log` 关闭旗，
    `cli.py:1846-1872` 默认调 `record_to`；`shadow.py:35` `OP_LOG_ACTIONS={parity:diff}`
    + `shadow.py:191-198` 白名单断言（6 个非法 action 实测全部 ValueError 且 broker 未被触达）
  - [x] **D4 过户费 decider**：新模块 `rq_engine/costs.py` —— `TransferFeeOverlay`
    **包住**框架已注册的股票 decider，把 0.001% 双向加进 `TransactionCost.other_fees`
    （框架现金扣减与资金校验都读 `.total`，`interface.py:745-752`）；只作用于 CS，
    装载挂 `POST_BEFORE_TRADING`（`ptpaper` priority=50 比 `sys_transaction_cost` 先
    start_up，`mod/__init__.py:68`，挂 start_up 根本看不到框架 decider）；
    后置门禁 `TransferFeeNotInstalled` 兜"没装上"（`runner.py:162-166`）。
    **验收信号达成**：22 万元单子两侧费用 **57.2 == 57.2、`fee_delta=0.0`、现金一致**
  - [x] "预期差逻辑收敛"：删 `FEE_TOLERANCE_RATIO`（相对 1%）→ `FEE_ABS_TOLERANCE=0.01`
    绝对容差；T32 旧用例前提被推翻，改写 + 新增（`tests/test_rqalpha_adapter.py:247-272`）
  - [x] T35 迁移留痕：`db_manager.py:20-29` 登记三类迁移 + `cli.py:1153-1166/1642`
    写 `op_log[data:migrate]`（取走即清空，重复调用不重复留痕）；
    顺带修 `daily_bars` 建表缺 `adjust` 列导致**每个新库都误报一次迁移**（`db_manager.py:120`）
  - [x] 测试 + `report-t38.md`：新增 23 条（18 条不需 rqalpha）+ 迁移留痕 3 条；
    e2e **16/16 PASS**（`spikes/e2e_t38.py`，含 S3 变异检查：overlay 装不上 →
    门禁停线 + `fee_delta=-2.2` + `cash_delta 2.2` 同时报）；
    全套 **461 passed/1 skipped**（有 rqalpha）与 **453 passed/9 skipped**（生产口径），
    基线 431/4 ⇒ 零回归
  - ⚠️ **交付时撞破我自己的 P1（T32 缺陷）**：产品化 `build_config` 从没带
    `sys_transaction_cost` ⇒ 引擎一直在用框架默认**佣金万八**（现网万 2.5）。
    T29/T32 没暴露是因为单子太小、两侧都撞 5 元最低佣金把差额盖住了
    （22 万元单差 121 元）。费率对齐留在 spikes 里没搬进产品件 = 我的疏漏，
    现按"费率进会话指纹"修复；副作用见 report-t38 §5.5（指纹变了，旧 state_dir 会判漂移）
- [x] **T39（Qoder，P0·M2 影子期启动）NEC 引擎环境落位**（`worker/t39`，**待 review**）
  - [x] NEC 独立引擎 venv `/home/nec/venvs/rq-engine`：按 lock **先 `numpy<2.4`（20s）
    再 rqalpha==6.4.2（182s）**，lock 收敛 2s ⇒ 41 包 / 700 MB / `pip check` 干净 /
    numpy 2.3.5 + pandas 3.1.0，x86-64-v1 上 h5py·scipy·matplotlib(Agg)·statsmodels·
    jsonpickle·logbook 全链 import 通过；现网交易 venv 仍无 rqalpha（未混装，实测 import 报
    ModuleNotFoundError）
  - [x] bundle 落位 `/home/nec/.rqalpha/bundle`：`rqalpha download-bundle`（202610 快照，
    16:59→17:02 约 3 min，3,537,975,667 B / 14 文件），**sha256 全表已记**
    （report-t39 §3，NEC 另存 `t39_bundle_sha256.txt`）；开发机 bundle 是同一份快照
    ⇒ 跨机对照无"两份数据不同"混淆因子。`check-bundle` 会提示"日线不完整"= 月度快照
    月中必然落后，行情由我们 `daily_bars_raw` 供，但 `ex_cum_factor` 同源 ⇒ 复权因子
    新鲜度仍是真约束（§4）
  - [x] `rq shadow-day` NEC 首跑（真实 2026-10-29 收盘原始价，5 s）：两侧现金
    **827,339.1198 == 827,339.1198**、费用 **44.8802 == 44.8802**、
    `cash_delta/fee_delta/fill_delta = 0`、`violations=[]`、2 成交 + 1 拒单（中文原因可读）、
    `evidence.cost.wrapped=["CS"] applied=8 added_total=6.90464`、gates ok；
    手算复核费用构成一致（佣金 21.47+21.68、过户费 0.8588+0.8674）。
    开发机同数据同场景复跑**逐字段相同**；`--no-op-log` 在 NEC 生效（op_log 0 行）
  - [x] `report-t39.md`（耗时/磁盘/首跑对账 + 逐条核销清单）；cron 只给草稿（§7），
    按派发"跑顺再提请"
  - ⚠️ **留痕没写现网账本**：`--account-db` 指到 scratch 库。理由是 `worker/t38` 未 review
    ＋红线不写账本，现网 `op_log` 至今 0 条 `parity:diff`；切换时机请拍板（§6-C）
  - 🔴 **两件阻塞影子期的发现（只报告不自修）**：
    ① 生产 `data.db.daily_bars_raw` = **0 行** ⇒ 派发说的"生产真数据"现在不成立，
    需另派"现网 raw 回填 + 每日采集"；
    ② raw 管道**参照窗口比采集窗口短**（采集 `days*2+10` 自然日 vs 母库 `LIMIT days` 交易日，
    `raw_feed.py:239-241` vs `mother.py:533-540`）⇒ scratch 实测 590 行只有 400 行验了料，
    其中 17 个未验料日是**母库明明有数据**的 8 月；而 `raw_flip_watch` 只看最后一次
    checked 距今几个交易日（`raw_feed.py:192-218`），`data raw-coverage --gate` 对此
    报 `violations=[]`（盲区）。细节与修法见 report-t39 §6-B
  - 另：现网母库其实可达（`/home/nec/stock-dashboard/data/db/stock_dashboard.db`），

---

## Batch 9 · 影子期正式运行（主 AI 派发 2026-10-29 深夜 · 批复见 questions.md）

> T38/T39 已验收合并（453+9skip 生产口径；NEC 引擎 venv 461+1）。三件拍板：
> 授权现网 raw 回填 / T39 §6-B 三缺陷派修 / 留痕现在切现网。
> **Qoder 四件连发：T41→T40→T42 有依赖顺序（先修管道再回填再挂 cron），T43 独立。**

- [x] **T41（Qoder，P0·先行）raw 管道加固**（`worker/t41`，**待 review**；T39 §6-B 三缺陷）
  - [x] 参照窗口对齐：新增 `raw_feed.fetch_window(days, today)` 作为采集/参照/审计
    **唯一的窗口定义**（顺带修掉默认 `fetch` 写死 `date.today()` 不看 `today` 参数的坑）；
    `mother.raw_close_map(..., since=)` 按日期下界取参照，老调用方 `days` 语义不变。
    **真机：同一库同一窗口 `checked 400 → 557`（590 行，拒收 0）**
  - [x] `raw_flip_watch` 逐日检查：`db.raw_date_flags()` 逐日台账 + 六态分类
    （`verified/today_pending/unflipped/waiting_reference/no_reference_available/audit_incomplete`，
    另有 `raw_stale/no_raw_data/reference_source_unavailable` 停线态）；
    CLI `raw-coverage` 新增 `audit` 块并进 `--gate`。
    **真机：T39 那份报 `violations=[]` 的库现在报 `unflipped_days=157`、10 只全停线**；
    新采的那份 `unflipped_days=0`、gate 绿（母库滞后的 23 天归 waiting 告警）
  - [x] `--no-mother-ref` 语义修正（选"真不读"而不是改名）：`raw_feed.reference_for()`
    是旗子→参照提供者的唯一映射，CLI 不再留 `reference=None`。
    **真机：带旗 `single_source=true`、`symbols_with_ref=0`、590 行全 `checked=0`，
    且闸口归因 `reference_source_unavailable` 停线**（不装没事）
  - [x] 回归测试 + `report-t41.md`：`tests/test_raw_pipeline_t41.py` 17 条，
    全套 **470 passed / 9 skipped**（基线 453/9 零回归）；
    `spikes/mutation_t41.py` 四条源码级变异（内层→外层/丢 since/旗失效/停线不生效）
    分别 1/1/2/3 条必红，还原后 55 条全绿
  - ⚠️ 真机跑出的**我自己引入的缺陷已当场修**：`flip_watch_with_mother` 误用
    `raw_close_map` 的外层 `{code:{date:row}}` ⇒ `ref_asof` 变成标的代码、
    `ds in have` 恒假，双向误判（漏报 unflipped + 7 只母库滞后标的被误停线）；
    已加形状回归用例锁死
  - ⚠️ 语义收紧是有意的：`--gate` 失败面变大（旧数据今天就该不合格）；
    母库不可达且窗口内一行都没验过 ⇒ 停线；T34 已验收的判据一条没放松（12 条全绿）
  - ➡️ 给 T40 的输入：现网 `daily_bars` = 27,951 行 / 38 只 / 2023-10-09…2026-10-29（每只 742 行），
    对齐深度约需 `--days 550`；`--days` 参数含义含糊（自然日换算），要不要加 `--start/--end`
    请主 AI 定。跨仓事实：同一轮里母库深市到 10-27、沪市只到 10-23（影子期会持续
    `waiting_reference` 告警，不停线）——若判定为母库同步问题需转 stock-dashboard 那条对话
- [x] **T40（Qoder，P0·依赖 T41）生产 raw 回填**（`worker/t40`，堆叠在 `worker/t41` 之上，
    **待 review 按序合并**；已授权行情库）
  - [x] 全池 38 只回填 `daily_bars_raw`：**0 行 → 28,339 行 / 2023-09-25…2026-10-29**，
    `checked=28,264`（**99.74%**）、**拒收 0**、口径标签全 `raw`；
    顺序是先探母库深度（10,548,954 行/5,193 只/2016-01-04…）→ **现网库副本干跑**
    （35 s 同参数）→ `data.db.pre-t40-20261029-181759.bak` 冷备 → 现网跑（44 s）
  - [x] **`data raw-coverage --gate` 全绿（退出码 0）**，`audit={audited:28339,
    verified:28264, unflipped_days:0, waiting_days:37, no_reference_days:0,
    incomplete_days:0, complete:true}`、`violations=[]`；
    `raw_flip_watch` 三态核对：37 行未验料**全部**是 `waiting_reference`
    （母库自己只更到 10-23/10-27，18 只在等），母库补上后必须由下一次 `fetch-raw` 翻转，
    否则逐日闸按 `checked_not_flipped` 停线（T41 设计）
  - [x] 回填后重跑 `rq shadow-day`（现网真数据，2 s）：买 000651/300750/600519 三笔
    （含创业板 20% 带）+ 卖 601318 无持仓被拒且原因可读 ⇒
    **两侧现金 743,559.3428 == 743,559.3428、费用 66.6572 == 66.6572、
    cash/fee/fill_delta 全 0、violations=[]**，`wrapped=["CS"]`；逐笔手算复核佣金+过户费一致
  - [x] 缺口与红线如实报告（report-t40 §2/§5/§6）：`daily_bars`(27,951、SUM(volume)/SUM(close)
    逐位不变) 与 `minute_bars`(2,304) **未被动过**；现网 `paper_account.db` 的
    `parity:diff`/`data:migrate` **仍是 0 行**（影子留痕写在 scratch，首个现网留痕归 T42）
  - ⚠️ 两点要你知道：①本次用的是 `worker/t41` 的管道（未 review）——main 的窗口缺陷
    会把"1/3 无从验料"固化进现网，所以按 T41→T40 顺序走；review 后若语义变动，
    重跑 `fetch-raw` 即可（幂等 upsert）。②`--days 560` 只是"覆盖 742 交易日"的倒推值，
    参数含义含糊，要不要加 `--start/--end` 等你定
  - 顺带发现（不在 T40 范围，未动）：`DataDBManager._connect()` 没设 `busy_timeout`，
    与 cron 并发写会直接 `SQLITE_BUSY` 报错而不是等
- [x] **T42（Qoder，P0·依赖 T40）~~影子期一周~~ → 切换前对账验证**（`worker/t42`，
    堆叠在 `worker/t40`→`worker/t41` 之上，**待 review 按序合并**；§5.6 节奏变更）
  - [x] **首个现网 `op_log[parity:diff]` 已落库**：id=327，
    `result={"cash_delta":0.0,"fee_delta":0.0,"fill_delta":0,
    "rqalpha_cash":743559.3428,"legacy_cash":743559.3428}`（写之前该库 parity 行数=0）；
    写前冷备 `paper_account.db.pre-t42-20261029-182221.bak`
  - [x] 全链三步 2026-10-29 实跑（采集 18:22 >15:35 → shadow-day 现网真数据 → `--gate`）：
    `cash/fee/fill_delta` 全 0、`violations=[]`、gate 退出码 0
    （`audit={audited:28339, verified:28264, unflipped:0, waiting:37, complete:true}`）；
    场景含创业板 20% 带（300750）与"无持仓卖出被拒且原因可读"；费用逐笔手算与两侧一致
  - [x] 账本红线核对（写现网账本必须留这一栏）：`account.cash` 71,922.004、
    持仓 5 只、`orders/fills/nav_history/agent_plans/conditional_orders` = 12/11/15/17/37
    **前后逐位不变**；`op_log` 325→327（只多 `data:raw_no_intraday_ref` + `parity:diff` 两行审计）；
    影子成交落在 `--shadow-dir`，与生产同路径直接 RuntimeError 的拒绝逻辑再次生效
  - [x] cron 草稿提请（report-t42 §5，15:40 三步，由主 AI 装）
  - ⚠️ **"切换就绪"我给成有条件**：这一跑照出一件 P1——**当日跨源对照（裁决 2 的 A 半边）
    在 ≥15:35 的采集时点上恒不成立**：`realtime.py:149-150` 非交易时段直接 return {}，
    `realtime.py:19/54` 的 180 秒新鲜度又丢掉腾讯收盘最后一笔（实测时间戳 16:14:45），
    而同刻 `fetch_batch` 能正常拿到 000651=37.58（与新浪收盘一致）⇒ 是我们自己的闸把防线关了；
    叠加 `source.py:76-104` 引擎取数口不查 `checked=1` ⇒ 当日 bar 无独立参照就被吃下。
    三个修法 (a) 盘后专用新鲜度 / (b) 采集挪盘中（不建议，新浪日K 15:35 才发）/
    (c) 当日那根必须 checked 才喂引擎（选它就得先做 (a)，否则每天该时段停摆）——
    **已进 questions.md 等拍板，我不自选**
  - ➡️ 待办：第二个验证日（10-30 15:40 后同一条链）由我跑或 §5 cron 装好后自动产出；
    另请一并定"影子期 cron 用 `stock_pool` 表 38 只还是 `config.yaml` 的 10 只"
- [ ] **T45（Qoder，P0·M3 切换方案）切换 runbook**（参照 `migration-nec.md`
  的经验与 checklist 传统，产出于 `docs/migration-m3.md`）：
  - [ ] 切换日流程：账本归档（`paper_account.db` 冷备+只读保留）→ **切换日
    新开账**（新引擎账本起点=切换日 NAV 对齐，历史只读可查）→ cron 换线
    （旧引擎 5 条停、新引擎线启用，**顺序铁律同 Pi→NEC：先停旧再启新**）→
    验证步（首单/费用/T+1/NAV/面板/留痕）
  - [ ] **回退方案**：config 开关一键切回旧线的精确步骤 + 回退后账本衔接
    说明（新开账 vs 归档账的边界）
  - [ ] 逐条 cron/服务/路径清单对照（漏一条=漏一个功能——T21 教训）
  - [ ] `report-t45.md`；**切换操作本身由主 AI 亲执行**
- [ ] **T46（Qoder，P0·切换后检查体系）检查清单 + 验证工具**：
  - [ ] `docs/m3-checklist.md`：第一小时 / 第一天 / 第一周三段逐项——
    T+1 冻结正确、费用三件套扣对（佣金地板/印花税单边/过户费）、NAV 收盘
    记录、条件单触发链、AI 计划执行链、审计留痕完整、面板数据一致、
    影子对账记录连续
  - [ ] 可自动化项做成命令（如 `python -m paper_trading.cli m3 verify`：
    逐项检查出绿勾/红叉），人工项写清判定方法
  - [ ] 每一项标注"红了怎么办"（回退 or 归因 or 容忍）——**任何钱类项红
    ⇒ 立即回退**（§5.6 底线）
  - [ ] `report-t46.md`；工具给 Hermes 审一遍再交付
- [ ] **T43（Qoder，P1·独立穿插）T28 架构地图催办**：`docs/architecture-map.md`
  （Batch 5 派发未交：模块清单工具统计、import 实测依赖图、L1/L2 现状 vs 目标、
  重复/死代码/测试盲区）——与 T40-42 无依赖，随时做
- [ ] **T44（Hermes，P0·审查）T38/T39 钱类件复核**：TransferFeeOverlay
  （H 系视角：套层重复挂载/基金类遗漏/费率变更时会话指纹漂移的恢复语义）+
  T39 三发现的严重性复核（漏验窗口/盲区/--no-mother-ref 是否还有第四件）+
  T38 自撞破的佣金 P1 修复复核（万 2.5 是否真的进了会话指纹与撮合两侧）；
  产出 `report-t44.md`，P0 → questions.md
