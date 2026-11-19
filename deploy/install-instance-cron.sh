#!/bin/sh
# 第二实例的 AI 交易员 cron 安装/核对（2026-11-19）。
#
# 为什么有这个脚本：一个"面板活着"的实例不等于"交易员活着"。某次部署把合并树
# 当只读面板用，三条 cron 只存在于机器的 crontab 里；后来一次 crontab 重写把
# 它们全部丢掉——面板继续 200，agent_plans 却停在三周前，全程没有任何告警。
# 把"该有哪些行"写进仓库，才能在重写之后一条命令核对回来。
#
# 只装三件（其余由实例自行决定）：
#   09:30-10:00  开盘执行窗口：只消费当天 pending 计划，无计划静默
#   16:30-18:30  收盘计划窗口：数据定稿到今天才做下一交易日计划
#   周六 10:00   交易日历同步（母库 trading_calendar 优先）
# 第二实例通常 engine.enabled=false、不跑 rq：不需要引擎/事件/rq 的 cron；
# 若要跑，必须与主实例的账本、var/、日志目录完全隔离。
#
# 用法：
#   INSTANCE_DIR=/srv/pt2 PYTHON=/srv/pt2/venv/bin/python \
#   SCHEME=mother POOL_FROM=screening POOL_LIMIT=15 \
#   sh deploy/install-instance-cron.sh
#
#   ... --check    # 只核对；缺行 exit 1（可放进部署后检查或 daily-check）
#
# 默认值取脚本所在仓库的根目录，即"给当前这份 clone 装 cron"。
set -u

DIR=${INSTANCE_DIR:-$(cd "$(dirname "$0")/.." && pwd)}
PY=${PYTHON:-$DIR/venv/bin/python}
SCHEME=${SCHEME:-}
POOL_FROM=${POOL_FROM:-}
POOL_LIMIT=${POOL_LIMIT:-}
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

if ! command -v crontab >/dev/null 2>&1; then
    echo "!! 没有 crontab：本脚本只在 Linux 生产机使用"
    exit 2
fi
if [ ! -x "$PY" ]; then
    echo "!! 找不到可执行的 python：$PY（用 PYTHON=/path/to/python 指定）"
    exit 2
fi

# --scheme 是全局参数，必须放在子命令之前；--pool-* 属于 agent tick
TICK="agent tick --json"
if [ -n "$SCHEME" ]; then TICK="--scheme $SCHEME $TICK"; fi
if [ -n "$POOL_FROM" ]; then TICK="$TICK --pool-from $POOL_FROM"; fi
if [ -n "$POOL_LIMIT" ]; then TICK="$TICK --pool-limit $POOL_LIMIT"; fi

TICK_CMD="cd $DIR && $PY -m paper_trading.cli $TICK >> logs/agent.log 2>&1"
CAL_CMD="cd $DIR && $PY -m paper_trading.cli calendar sync --json >> logs/calendar.log 2>&1"

OPEN_LINE="30,35,40,45,50,55 9 * * 1-5 $TICK_CMD"
PLAN_LINE="*/5 16-18 * * 1-5 $TICK_CMD"
CAL_LINE="0 10 * * 6 $CAL_CMD"

mkdir -p "$DIR/logs"
cur="$(crontab -l 2>/dev/null || true)"

missing=""
for line in "$OPEN_LINE" "$PLAN_LINE" "$CAL_LINE"; do
    if ! printf '%s\n' "$cur" | grep -Fxq "$line"; then
        missing="$missing$line
"
    fi
done

if [ -z "$missing" ]; then
    echo "OK 三条任务齐全：$DIR"
    exit 0
fi
if [ "$CHECK" = "1" ]; then
    echo "!! 缺少任务（实例 $DIR）："
    printf '%s' "$missing" | sed 's/^/   /'
    exit 1
fi

BACKUP="$HOME/crontab-backup-$(date +%Y%m%d-%H%M%S).txt"
printf '%s\n' "$cur" > "$BACKUP"
{
    [ -n "$cur" ] && printf '%s\n' "$cur"
    printf '%s' "$missing"
} | crontab -

echo "OK 已补任务（原 crontab 备份：$BACKUP）："
printf '%s' "$missing" | sed 's/^/   /'
