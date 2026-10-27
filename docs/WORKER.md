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

**状态**：Batch 1 T1–T4 + T5' 已交付；**T6 已交付 → akquant 不反超（缺涨跌停带），rqalpha 定案**；主 AI 并行处理 H-1..H-8 复核修复。
  注：T6 的两处未做项见 `report-t6.md` §3（Pi 真机安装由主 AI 一枪收口；akquant 策略执行验证约半天，不在否决路径上）。
