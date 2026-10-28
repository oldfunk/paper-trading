# 生产迁移 runbook：Pi → NEC（2026-10-28）

> 背景：Pi 过劳实锤（load 4.4 持续满载 / free 59MB / 用户观察连续自重启）。
> 迁移目标：`192.168.50.60`（pi） → `192.168.50.71`（nec，Linux Mint x86_64 /
> 2 核 / 5.9G RAM / Py 3.12.3 / 370G 空闲）。
> **最高风险：双活实例重复下单**——同一 AI 决策在两台机器各执行一遍。
> H-5 的 consume-once 防的是单机崩溃重跑，防不了双活。

## 规格对比（迁移收益）

| | Pi 4 | NEC |
|---|---|---|
| 负载 | 4 核 load 4.4（满载） | 2 核 load 0.01 |
| 内存 | 954MB，free 59MB | 5.9GB，avail 4.6GB |
| 磁盘 | SD 59G（写磨损=重启元凶候选） | 468G（bundle 3.3GB 无压力） |
| 架构 | aarch64 | x86_64（whl 通用） |

## 迁移对象清单（逐条核销，T21 对照 Pi 现状）

| # | 对象 | Pi 现状 | NEC 方式 |
|---|---|---|---|
| 1 | 代码 | `~/paper-trading` @ v0.7.10+ | git clone（同 origin） |
| 2 | venv | Py3.13.5 | 重建（**禁止拷贝 venv**，跨架构二进制不兼容） |
| 3 | 账本 | `paper_account.db` 452K | scp（停 cron 后拷，见 P1） |
| 4 | 行情库 | `data.db` 6.4M | scp（同上） |
| 5 | cron | paper-trading 5 条（见下） | **P1 尾部由主 AI 启用** |
| 6 | dashboard | systemd `paper-trading-dashboard` | 重建 unit（同名同端口 8080） |
| 7 | stock-dashboard | Pi 上另一项目（cron 年报回填） | **不迁**（用户未指定，留 Pi） |

### Pi cron 清单（逐条搬，漏一条=漏一个功能）

```
# paper-trading（迁 NEC）
* 9-15 * * 1-5        engine --once          # 引擎每分钟
30,35,40,45,50,55 9   agent tick             # 早盘计划
*/5 16-18             agent tick             # 盘后计划窗口
1-59 9-11,13-14       agent event            # 事件回顾每分钟
0 10 * * 6            calendar sync          # 周六日历同步
# stock-dashboard（留 Pi，不迁）
30 2 * * *            backfill_financial_extra.py
```

## 三阶段执行

### Phase 0 —— NEC 环境搭建（T20/Qoder，Pi 不动）

1. clone + venv + 依赖 + **326 passed, 1 skipped** 与基线对齐
2. dashboard systemd 起服务，`curl 127.0.0.1:8080/` → 200
3. **不启用任何 paper-trading cron**（红线）
4. 冷备份 Pi 数据库一份（本地留底）：`scp pi:~/paper-trading/*.db ./pi-backup-<date>/`

### Phase 1 —— 切换窗口（主 AI 亲自执行）

**时机红线：19:00 之后或周末**——Pi cron 最后活动是 18:55 的 agent tick；
盘中/活动期切换会漏决策或双写。

```
① Pi 停 paper-trading cron（stock-dashboard 条目保留）：
   crontab -l | grep -v 'paper-trading' → crontab -   # 或手工注释 5 条
② 确认无残留进程：pgrep -f paper_trading 应为空
③ 拷数据：scp pi:~/paper-trading/paper_account.db pi:~/paper-trading/data.db → NEC ~/paper-trading/
④ NEC 验证（启用 cron 前）：
   - sqlite3 paper_account.db "SELECT COUNT(*) FROM orders;"  # 与 Pi 同数
   - venv/bin/python -m paper_trading.cli ... dry-run         # 不写库冒烟
   - curl 127.0.0.1:8080/ → 200
⑤ NEC 启用 5 条 cron（crontab -e 手工贴，路径改 /home/nec/...）
⑥ 观察下一轮 cron 日志无异常 → 切换完成
```

**顺序铁律：① 在 ⑤ 之前。宁可空窗几分钟（漏一轮分钟级 tick，无资金影响），
不可双活一秒（重复下单）。**

### Phase 2 —— 观察期（2-3 交易日）

- NEC 生产运行；Pi 冻结为**只读回退副本**（cron 保持停用，db 不再写入）
- 每日核对：agent_decisions 有新记录、dashboard 正常、engine.log 无错误
- 3 天无异常 → Pi 上 paper-trading 正式退役（保留目录只读，不删）
- 异常回退：Pi `crontab -l` 恢复 5 条 → 停 NEC cron → Pi 接管（数据从 NEC 拷回）

## 数据一致性验证（T21 重点审查）

- 拷贝前：Pi `orders`/`nav_history`/`agent_decisions`/`op_log` 四表 COUNT
- 拷贝后：NEC 同四表 COUNT 一致 + `PRAGMA integrity_check` = ok
- 观察期末：Pi（冻结值） vs NEC（新增量） 差值 = 观察期新增行数，逐表核对
