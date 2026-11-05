#!/bin/sh
# T80（M4-②）：每日核对的**真身**在生产机上跑（CI 没有 rqalpha/真库，见 report-t78 §4）。
#
# 装法（运维/主 AI 执行，我没有动 crontab）：
#   30 15 * * 1-5 /home/nec/paper-trading/deploy/daily-check.sh >> /home/nec/paper-trading/logs/daily_check.log 2>&1
# 15:30 在收盘后、旧线 `agent tick` 的收盘窗口之前。退出码就是结论：
# 0=绿/黄，1=有红，3=钱类红（§5.6 的"立即回退"评估线）——与 `m3 verify` 同一把尺子。
# 连续 5 个交易日 0/1 且红都能归因，就是 M4 验收口径①要的那张表。
#
# 只读：三个 rq 侧核对项一律 mode=ro 开库，不写 op_log、不碰状态目录。
# JSON 落成一文件一行的 JSONL（观察报告按天聚合红/黄/绿就靠它）。
set -u
ROOT=/home/nec/paper-trading
PY=$ROOT/venv/bin/python
OUT=$ROOT/logs/daily_check.jsonl
ERR=$ROOT/logs/daily_check.err
mkdir -p "$ROOT/logs"
cd "$ROOT" || exit 1

RC=0
RAW=$(mktemp)
$PY -m paper_trading.cli m3 daily-check --json > "$RAW" 2>>"$ERR" || RC=$?
# CLI 前面可能还有日志行：从第一个 `{` 起解，再压成一行落盘（多行 JSON 会把 JSONL 打糊）
$PY -c '
import json, sys
raw = open(sys.argv[1], encoding="utf-8").read()
i = raw.find("{")
try:
    obj = json.loads(raw[i:]) if i >= 0 else None
except json.JSONDecodeError:
    obj = None
if not isinstance(obj, dict) or "data" not in obj:
    sys.stderr.write("daily-check: 输出里没解出 JSON（见 %s）\n" % sys.argv[1])
    sys.exit(2)
rec = dict(obj["data"])
rec["exit_code"] = int(sys.argv[2])
rec["at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
print(json.dumps(rec, ensure_ascii=False))
' "$RAW" "$RC" >> "$OUT" || echo "[daily-check] JSONL 落盘失败（结论仍看退出码）"
rm -f "$RAW"

echo "[daily-check] $(date -Is) exit=$RC"
if [ "$RC" -ge 1 ]; then
  echo "[daily-check] 详情见 $OUT 最后一行；钱类红(3)=按 §5.6 评估回退"
fi
exit "$RC"
