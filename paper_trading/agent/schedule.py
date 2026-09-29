"""AI 定时：用户可配的决策时段与每日次数。

cron 只负责高频 tick（如每 15 分钟叫一次 `agent run`），本模块在进程内
判定“现在该不该真跑”：时段未到/次数用完一律跳过（记流水，不问 LLM）。

用户配置只写 gitignored 的 `strategy.local.yaml`（与投资方案同一文件）：
    agent_schedule:
      slots: ["16:45"]   # 每日触发时刻 HH:MM，可多个，逗号分隔也认
      max_runs: 1        # 每日最多实质决策次数

缺省 slots=["16:45"]（收盘 15:00 + 数据源落定余量，保守时间），max_runs=1
（保持历史行为：一天只决一次）。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

LOCAL_FILE = "strategy.local.yaml"

DEFAULT_SLOTS = ["16:45"]
DEFAULT_MAX_RUNS = 1
MAX_SLOTS = 5
MAX_RUNS = 5


def _now() -> datetime:
    """当前时间（单测 monkeypatch 点）。"""
    return datetime.now()


def parse_slots(text: str | list) -> list[str]:
    """解析时刻表：认 "16:45,17:30" 或 ["16:45"]；校验 HH:MM，去重排序。"""
    if isinstance(text, str):
        parts = [p.strip() for p in text.replace("；", ",").replace(";", ",").split(",")]
    elif isinstance(text, (list, tuple)):
        parts = [str(p).strip() for p in text]
    else:
        raise ValueError("时段格式错误（示例：16:45,17:30）")
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        hh_mm = p.split(":")
        if len(hh_mm) != 2 or not all(x.isdigit() for x in hh_mm):
            raise ValueError(f"时刻格式错误（须为 HH:MM）：{p}")
        hh, mm = int(hh_mm[0]), int(hh_mm[1])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError(f"时刻超出范围（00:00-23:59）：{p}")
        out.append(f"{hh:02d}:{mm:02d}")
    out = sorted(set(out))
    if not out:
        raise ValueError("至少保留一个触发时刻")
    if len(out) > MAX_SLOTS:
        raise ValueError(f"触发时刻最多 {MAX_SLOTS} 个")
    return out


def parse_max_runs(v) -> int:
    """校验每日次数：1-5 的整数（防 cron 高频 tick 失控；小数/布尔一律拒绝）。"""
    if isinstance(v, bool):
        raise ValueError(f"每日次数须为 1-{MAX_RUNS} 的整数：{v}")
    s = v if isinstance(v, str) else str(v) if isinstance(v, int) else None
    if s is None or not s.strip().isdigit():
        raise ValueError(f"每日次数须为 1-{MAX_RUNS} 的整数：{v}")
    n = int(s.strip())
    if not (1 <= n <= MAX_RUNS):
        raise ValueError(f"每日次数须为 1-{MAX_RUNS} 的整数：{v}")
    return n


def _read_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def load_schedule(root: str | Path = ".") -> dict:
    """读定时配置；缺失/损坏一律回默认（fail-open 用默认，不阻断）。"""
    raw = _read_yaml(Path(root) / LOCAL_FILE).get("agent_schedule") or {}
    try:
        slots = parse_slots(raw.get("slots", DEFAULT_SLOTS))
    except ValueError:
        slots = list(DEFAULT_SLOTS)
    try:
        max_runs = parse_max_runs(raw.get("max_runs", DEFAULT_MAX_RUNS))
    except ValueError:
        max_runs = DEFAULT_MAX_RUNS
    return {"slots": slots, "max_runs": max_runs,
            "source": "local" if raw else "default"}


def save_schedule(root: str | Path, slots_text: str | list,
                  max_runs) -> tuple[bool, str]:
    """保存定时配置（只写 strategy.local.yaml；校验失败不落盘）。"""
    try:
        slots = parse_slots(slots_text)
        n = parse_max_runs(max_runs)
    except ValueError as e:
        return False, str(e)
    try:
        import yaml  # type: ignore
    except ImportError:
        return False, "缺少 pyyaml"
    p = Path(root) / LOCAL_FILE
    raw: dict = _read_yaml(p)
    raw["agent_schedule"] = {"slots": slots, "max_runs": n}
    try:
        p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        return True, f"已保存：每日 {n} 次，时刻 {','.join(slots)}"
    except Exception as e:
        return False, f"写入失败：{e}"


def due_slots(slots: list[str], now: Optional[datetime] = None) -> list[str]:
    """此刻已到达的时段（HH:MM 字符串比较即可，同为零填充）。"""
    hm = (now or _now()).strftime("%H:%M")
    return [s for s in slots if s <= hm]


def consumed_slots(slots: list[str], live_times: list[str]) -> set[str]:
    """已被消费的时段：每次实盘实质决策消费其时刻前最近一个时段。"""
    done: set[str] = set()
    for t in live_times:
        past = [s for s in slots if s <= t and s not in done]
        if past:
            done.add(past[-1])
    return done


def check(slots: list[str], max_runs: int, live_times: list[str],
          now: Optional[datetime] = None) -> tuple[bool, str, Optional[str]]:
    """定时闸：返回 (放行, 原因, 本次消费时段)。

    - 原因：ok / not-in-schedule（时段未到或已消费完）/ max-runs-reached
    """
    live_times = live_times or []
    if len(live_times) >= max_runs:
        return False, "max-runs-reached", None
    due = [s for s in due_slots(slots, now) if s not in consumed_slots(slots, live_times)]
    if not due:
        return False, "not-in-schedule", None
    return True, "ok", due[-1]
