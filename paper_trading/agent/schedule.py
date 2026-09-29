"""AI 定时：用户可配的决策队列。

cron 只负责高频 tick（如每 15 分钟叫一次 `agent run`），本模块在进程内
判定“现在该不该真跑”：队列按时刻顺序消费，每个时段每天最多跑一次，
时段没到/队列跑完一律跳过（记流水，不问 LLM）。

用户配置只写 gitignored 的 `strategy.local.yaml`（与投资方案同一文件）：
    agent_schedule:
      slots: ["16:45"]   # 触发时刻队列 HH:MM，可多个，逗号分隔也认

缺省 slots=["16:45"]（收盘 15:00 + 数据源落定余量，保守时间）。
跑几次 = 配几个时段，不设独立次数（单时段配多次跑不起来，属无效配置）。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

LOCAL_FILE = "strategy.local.yaml"

DEFAULT_SLOTS = ["16:45"]
MAX_SLOTS = 5


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
    """读定时配置；缺失/损坏一律回默认（fail-open 用默认，不阻断）。

    旧版残留的 max_runs 字段直接忽略（队列式下次数 = 时段数）。
    """
    raw = _read_yaml(Path(root) / LOCAL_FILE).get("agent_schedule") or {}
    try:
        slots = parse_slots(raw.get("slots", DEFAULT_SLOTS))
    except ValueError:
        slots = list(DEFAULT_SLOTS)
    return {"slots": slots, "source": "local" if raw else "default"}


def save_schedule(root: str | Path, slots_text: str | list) -> tuple[bool, str]:
    """保存定时队列（只写 strategy.local.yaml；校验失败不落盘）。"""
    try:
        slots = parse_slots(slots_text)
    except ValueError as e:
        return False, str(e)
    try:
        import yaml  # type: ignore
    except ImportError:
        return False, "缺少 pyyaml"
    p = Path(root) / LOCAL_FILE
    raw: dict = _read_yaml(p)
    raw["agent_schedule"] = {"slots": slots}
    try:
        p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        return True, f"已保存队列（每日 {len(slots)} 次）：{','.join(slots)}"
    except Exception as e:
        return False, f"写入失败：{e}"


def due_slots(slots: list[str], now: Optional[datetime] = None) -> list[str]:
    """此刻已到达的时段（HH:MM 字符串比较即可，同为零填充）。"""
    hm = (now or _now()).strftime("%H:%M")
    return [s for s in slots if s <= hm]


def consumed_slots(slots: list[str], live_times: list[str]) -> set[str]:
    """已被消费的时段（兼容口径）：每次实盘实质决策消费其时刻前最近一个时段。

    仅用于无 slot 标记的老流水回退；新流水直接记 slot（见 AgentTrader），
    不走时间推断，避免双时钟（mock 时间 vs 真实落库时间）错位。
    """
    done: set[str] = set()
    for t in live_times:
        past = [s for s in slots if s <= t and s not in done]
        if past:
            done.add(past[-1])
    return done


def check(slots: list[str], fired: set[str] | list[str],
          now: Optional[datetime] = None) -> tuple[bool, str, Optional[str]]:
    """定时闸：返回 (放行, 原因, 本次消费时段)。

    队列式：放行最早一个已到未消费的时段（保序）；无可跑时
    返回 not-in-schedule（时段未到，或全天队列已跑完）。
    fired 为今日已消费时段集合（由流水中的 slot 标记得出，与时钟无关）。
    """
    done = set(fired or [])
    due = [s for s in due_slots(slots, now) if s not in done]
    if not due:
        return False, "not-in-schedule", None
    return True, "ok", due[0]
