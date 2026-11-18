# 第二实例运维手册（同一台机器、多份账本）

> 创建：2026-11-19 · 上游：母项目合并树（dashboard :8081）断档事故 · 状态：runbook

## 1. 什么时候需要这份文档

同一台机器上跑**不止一份** paper-trading：例如

- `~/paper-trading`：独立实例（:8080），全套 cron（tick / event / engine / rq / daily-check）；
- 某个父项目 vendored 进来的那份（:8081）：**只做 AI 交易员**（一日一决策），
  面板与账本是它自己的，母库只读。

两者共用母库数据、共用同一台机器，但**账户、`data.db`、`paper_account.db`、
`var/`、`logs/` 必须各自独立**。这不是"多开一个面板"，而是"多一个交易员"。

## 2. 一条血泪前提：面板 200 ≠ 交易员活着

2026-10-23 → 11-19，一个合并实例就这样静默停摆了三周：

| 证据 | 值 |
|---|---|
| `agent_plans` 最后一条 | 10-26（10-23 16:47 生成），一直 `pending` |
| `op_log` 最后一条 | 10-23 16:47 `ai:plan` |
| 最后一笔成交 | 10-08 |
| 面板 `/`、`/api/status` | 全程 200，心跳过期也只是显示 `agent: stale` |
| 机器 crontab | 只剩另一实例的任务，本实例三条全丢 |

根因不是代码：**运行时配置只存在于 crontab**，一次重写就没了。
教训：任何"计划/成交停了但服务还在"的现象，先查 crontab，再怀疑代码。

## 3. 三条 cron（最小集）

```sh
# 开盘执行（09:30-10:00；只消费当天 pending 计划，无计划静默）
30,35,40,45,50,55 9 * * 1-5 cd <实例根> && <python> -m paper_trading.cli <scheme/pool 参数> agent tick --json >> logs/agent.log 2>&1
# 收盘计划（16:30-18:30；数据定稿到今天才做下一交易日计划）
*/5 16-18 * * 1-5 cd <实例根> && <python> -m paper_trading.cli <scheme/pool 参数> agent tick --json >> logs/agent.log 2>&1
# 交易日历（周六；母库 trading_calendar 优先）
0 10 * * 6 cd <实例根> && <python> -m paper_trading.cli calendar sync --json >> logs/calendar.log 2>&1
```

- `<scheme/pool 参数>`：价值路线常见 `--scheme mother --pool-from screening --pool-limit 15`。
  注意 `--scheme` 是**全局参数**，必须在 `agent tick` 之前。
- CWD 必须是实例根：`data.db` / `paper_account.db` / `var/` / `logs/` 都是相对 CWD 的。
- 用 `deploy/install-instance-cron.sh` 安装，`--check` 核对（幂等、改动前备份 crontab）：

```sh
INSTANCE_DIR=/srv/pt2 PYTHON=/srv/pt2/venv/bin/python \
SCHEME=mother POOL_FROM=screening POOL_LIMIT=15 \
sh deploy/install-instance-cron.sh

sh deploy/install-instance-cron.sh --check      # 缺行 exit 1
```

## 4. 不需要装的东西（第二实例默认）

| 组件 | 默认 | 原因 |
|---|---|---|
| `engine --once` / `engine run` | 不装 | `config.yaml` 的 `engine.enabled=false` 是发布默认；价值路线是收盘计划 + 开盘执行 |
| `rq tick` / `rq nav-close` | 不装 | 需要 rqalpha 独立 venv 与 `var/rq_state`；第二实例通常没有，且两套状态目录不能混 |
| `agent event` | 不装 | 盘中事件复盘会额外调 LLM；第二实例按"一日一决策"跑，预算留给收盘计划 |
| `daily-check.sh` | 建议装 | 它只读核对账本/场景，不交易；至少要有人知道"昨天该交易却没交易" |

## 5. systemd 面板单元

面板进程与交易员是**两件事**：面板挂了 cron 仍会在窗口内跑；反之亦然。
启动命令里的库路径**用绝对路径**，不要依赖 WorkingDirectory：

```ini
[Service]
WorkingDirectory=/srv/pt2
ExecStart=/srv/pt2/venv/bin/python -m paper_trading.dashboard \
  --port 8081 \
  --data-db /srv/pt2/data.db \
  --account-db /srv/pt2/paper_account.db \
  --secrets /srv/pt2/secrets.local.json
```

原因：`TradingBridge` 用相对路径时会 `sqlite3.connect` + `CREATE TABLE IF NOT EXISTS`，
Workdir 错位会在错误目录静默建出空库（面板显示空白、下单写空库）。
相关加固提案见 issue oldfunk/paper-trading#3。

## 6. 验收清单（每个交易日可自动核对）

```sh
# 1) cron 齐全
sh deploy/install-instance-cron.sh --check

# 2) 收盘窗口之后：应有目标日为下一交易日的 pending 计划
sqlite3 paper_account.db "select id, plan_date, status, created_at from agent_plans order by id desc limit 3;"

# 3) 开盘窗口之后：当天计划应转 done（或如实记录拒单原因）
sqlite3 paper_account.db "select id, plan_date, status, executed_at from agent_plans order by id desc limit 3;"

# 4) 账本有流水（不是只有面板）
sqlite3 paper_account.db "select id, timestamp, action, ok from op_log order by id desc limit 5;"

# 5) 日历缓存覆盖今天（缺失时 tick 会 fail-closed 跳过）
sqlite3 paper_account.db "select 1" >/dev/null; cat var/trading_calendar.json | head -c 200
```

建议把 1)+2) 做进收盘后的 daily-check：**"计划窗口过去了但计划日期还停在昨天"**
就是本次事故最直接的机器判据。

## 7. 已知的界面噪音

`agent schedule list` 仍会显示旧队列（如 `16:45 trade`，`source: default`），
但运行时**不再读取它**（窗口写死在 `agent/runtime.py`）。面板文案已经注明
"旧定时队列已停用"，CLI 输出尚未同步——看到这条队列不代表调度生效，以
`agent_plans` / `op_log` 的时间戳为准。
