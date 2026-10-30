"""M3b 意图队列：旧线 evaluate 触发的意图 → rq 引擎分钟会话执行。

分工不变（AGENTS 规则纪律 1）：**判定在 L2**（rules.evaluate 免费快），
**执行/撮合在 L1**（rqalpha 分钟会话，T+1/涨跌停/费用全是框架代码）。
executor=rq 时 engine runner 把触发意图入队（不直接撮合），
`rq tick` 每 5 分钟消费：pending 意图 → run_session(1m, intraday_resume)
→ ledger 落账 → 意图标记。

幂等双保险（T52）：① intent_key（symbol[:6]_SIDE_vol）与账本当日 orders
对照跳过已执行；② 意图表 status 推进（queued → executed/rejected）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rq_intents (
    biz_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    volume INTEGER NOT NULL,
    rule_ref TEXT,
    detail TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    result TEXT
)
"""

KEY_FMT = "{code}_{side}_{volume}"


def intent_key(symbol, side: str, volume: int) -> str:
    code = str(symbol).split(".")[0][:6].zfill(6)
    return KEY_FMT.format(code=code, side=str(side).upper(), volume=int(volume))


def _conn(db_path: str | Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path), timeout=15)
    c.execute("PRAGMA busy_timeout=15000")
    c.executescript(_SCHEMA)
    return c


def enqueue(db_path: str | Path, symbol, side: str, volume: int,
            rule_ref: str = "", detail: dict | None = None) -> str:
    """入队一个意图（同 key 未决意图去重——引擎侧已有防重，这里是第二道）。"""
    key = intent_key(symbol, side, volume)
    ts = datetime.now().isoformat(timespec="seconds")
    with _conn(db_path) as c:
        row = c.execute(
            "SELECT biz_id FROM rq_intents WHERE biz_id=? AND status='pending'",
            (key,)).fetchone()
        if row:
            return key                    # 已有待决同款意图——幂等返回
        c.execute(
            "INSERT OR IGNORE INTO rq_intents"
            " (biz_id, symbol, side, volume, rule_ref, detail, status, created_at)"
            " VALUES (?,?,?,?,?,?,'queued',?)",
            (key, str(symbol).split(".")[0], str(side).upper(), int(volume),
             rule_ref, json.dumps(detail or {}, ensure_ascii=False), ts))
    return key


def pending(db_path: str | Path) -> list[dict]:
    with _conn(db_path) as c:
        rows = c.execute(
            "SELECT biz_id, symbol, side, volume FROM rq_intents"
            " WHERE status IN ('pending','queued') ORDER BY created_at").fetchall()
    return [{"biz_id": r[0], "symbol": r[1], "action": r[2].lower(),
             "volume": int(r[3])} for r in rows]


def mark(db_path: str | Path, biz_id: str, status: str,
         result: dict | None = None) -> None:
    with _conn(db_path) as c:
        c.execute("UPDATE rq_intents SET status=?, result=? WHERE biz_id=?",
                  (status, json.dumps(result or {}, ensure_ascii=False), biz_id))


def executed_keys_from_ledger(db_path: str | Path, day: str) -> set[str]:
    """账本当日已成交意图 key（rq 侧落账的 orders/fills 反推）。

    归一到 intent_key 格式（symbol[:6]_SIDE_vol）——T52-R4 的防重复语义：
    只要今天这个 key 已有成交，队列里的同款意图一律跳过。
    """
    keys: set[str] = set()
    p = Path(db_path)
    if not p.exists():
        return keys
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        src = "fills" if "fills" in tables else "orders"
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({src})")}
        day_col = "timestamp" if "timestamp" in cols else (
            "trade_date" if "trade_date" in cols else "date")
        side_col = "direction" if "direction" in cols else "side"
        rows = c.execute(
            f"SELECT symbol, {side_col}, volume FROM {src}"
            f" WHERE {day_col} LIKE ?", (f"{day}%",)).fetchall()
        for sym, side, vol in rows:
            s = str(side)
            # fills.direction 是 int（paper_broker：1 买 / -1 卖）；orders.side 可能是文本
            norm = "BUY" if s in ("1", "buy", "BUY", "B") else "SELL"
            keys.add(intent_key(sym, norm, vol))
    finally:
        c.close()
    return keys
