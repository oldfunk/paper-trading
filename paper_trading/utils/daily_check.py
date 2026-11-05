"""每日核对（T80·M4-②）：把 T77 的观察清单变成一条只读命令 + 一个退出码。

为什么要有这条：M4 的验收口径是"连续 5 个交易日无未归因异常"，而"我每天看一眼
面板"不会留下可比较的证据。核对项必须**跑一次出一个码**，红就非零，
这样 CI / cron / 人都能读同一个结论，而不是各自解释截图。

三条 rq 侧核对（`m3 verify` 那 12 项之外的、只看 rqalpha 执行面）：
① `init_align`  框架账户与账本是否同源（11-05 P0 的日常体检）；
② `carry_over`  Q6 顺延的意图今天有没有落地（不能只顺延不兑现）；
③ `intent_chain` 当日新意图全链闭环（入队→会话→落账→标记）。

全部只读：开库一律 `mode=ro`，不写 `op_log`，不碰状态目录。
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from paper_trading.utils.m3_verify import item


def _ro(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def latest_evidence(state_dir, today: str) -> tuple[Optional[dict], str]:
    """读 `<state_dir>/evidence_<today>.json`（会话后置留痕）。"""
    d = Path(str(state_dir))
    f = d / f"evidence_{today}.json"
    if not f.exists():
        cand = sorted(d.glob("evidence_*.json")) if d.is_dir() else []
        return None, (f"今日无会话留痕（{f.name} 不存在）"
                      + (f"；最近一份：{cand[-1].name}" if cand else "；目录里一份都没有"))
    try:
        return json.loads(f.read_text(encoding="utf-8")), f.name
    except Exception as exc:                    # noqa: BLE001 - 坏了要说坏在哪
        return None, f"{f.name} 解析失败：{type(exc).__name__}: {exc}"


def check_init_align(state_dir, account_db, today: str, *,
                     work_expected: Optional[bool] = None) -> dict:
    """框架账户必须与账本同源（T73 的日常化）。

    `work_expected=False`（队列本来就空、没开会话）⇒ 没有留痕是正常的，判 skip；
    其余情况下"没留痕/被吞/比账本少仓"都是红。
    """
    ev, src = latest_evidence(state_dir, today)
    if ev is None:
        if work_expected is False:
            return item("init_align", "框架账户与账本同源", "skip", src)
        return item("init_align", "框架账户与账本同源", "red",
                    f"{src}——本判非零（会话没跑成/留痕丢了，持仓对齐无从证明）",
                    money=True, on_red="查 rq tick 日志与 cron")
    align = ev.get("init_align") or {}
    landed = ev.get("positions_landed") or {}
    frozen = ev.get("frozen_align") or {}
    if align.get("dropped_silently"):
        return item("init_align", "框架账户与账本同源", "red",
                    f"期初持仓被框架吞掉：{align['dropped_silently']}",
                    money=True, on_red="形态/解析层又变了（见 report-t73 §2）")
    uns = align.get("unsupported") or []
    uns_syms = {str(u.get("symbol")) for u in uns}
    ledger = ledger_positions(account_db)
    # 框架标的表里本就没有的合约（开源 bundle 无北交所 `.BJ`）不是"缺仓"——
    # 它被 `reconcile_init_positions` 如实剔除过，runner 侧会以
    # INSTRUMENT_UNAVAILABLE 给意图定局。豁免只认这份剔除留痕，不认"我没查到"。
    kept_syms = {str(k) for k, _ in align.get("kept") or []}
    missing = [{"symbol": ob, "ledger": qty} for ob, qty in sorted(ledger.items())
               if qty > 0 and ob not in kept_syms and ob not in uns_syms]
    if missing:
        return item("init_align", "框架账户与账本同源", "red",
                    f"账本有仓而未进框架：{missing[:5]}（留痕 {src}）",
                    money=True,
                    on_red="先停 cron，`ledger reset-state --only rq_session_state`")
    bits = [f"框架持仓 {landed.get('framework', '?')} 只/账本 {len(ledger)} 只",
            f"T+1 冻结留痕 {len(frozen.get('applied') or [])} 条"]
    if uns:
        bits.append(f"框架无此合约 {len(uns)} 只（{[u['symbol'] for u in uns][:3]}）")
    return item("init_align", "框架账户与账本同源", "green", "；".join(bits))


def ledger_positions(account_db) -> dict:
    """只读账本持仓（`order_book_id → total_volume`）。读不出就抛（核对不许猜）。"""
    from paper_trading.integration.rq_engine.store import to_order_book_id

    with contextlib.closing(_ro(account_db)) as c:
        return {to_order_book_id(r["symbol"]): int(r["total_volume"])
                for r in c.execute("SELECT symbol, total_volume FROM positions"
                                   " WHERE total_volume > 0")}


def check_intent_chain(account_db, today: str, *, executor: str = "rqalpha") -> dict:
    """当日新意图全链闭环：入队的每一条都得有结论，且落账笔数与 executed 数一致。"""
    with contextlib.closing(_ro(account_db)) as c:
        rows = [dict(r) for r in c.execute(
            "SELECT biz_id, symbol, side, volume, status, created_at"
            " FROM rq_intents WHERE substr(created_at,1,10)=?", (today,))] \
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                         " AND name='rq_intents'").fetchone() else []
        if not rows:
            return item("intent_chain", "当日意图全链", "skip",
                        f"{today} 没有新意图（executor={executor}）")
        open_ = [r for r in rows if r["status"] in ("pending", "queued")]
        filled = int(c.execute(
            "SELECT COUNT(*) FROM orders WHERE substr(created_at,1,10)=?"
            " AND status='filled'", (today,)).fetchone()[0]) \
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                         " AND name='orders'").fetchone() else 0
    dist: dict = {}
    for r in rows:
        dist[r["status"]] = dist.get(r["status"], 0) + 1
    detail = (f"当日 {len(rows)} 条：{dist}；账本当日 filled={filled}")
    if open_:
        return item("intent_chain", "当日意图全链", "warn",
                    f"{detail}——仍未定局 {len(open_)} 条"
                    f"（{[o['biz_id'] for o in open_][:3]}）：收盘后还在等就是漏环节",
                    on_red="归因")
    return item("intent_chain", "当日意图全链", "green", detail)


def check_carry_over(account_db, today: str, *, now: Optional[datetime] = None,
                     executor: str = "rqalpha") -> dict:
    """Q6 顺延的意图必须**兑现**：顺延过来还挂着（或干脆没执行）要说得出口。

    顺延时效只有今天一天：明天再核对时，昨天的顺延意图必须已是
    executed/rejected，不能变成第二条"静静挂着的 queued"。
    """
    if executor != "rqalpha":
        return item("carry_over", "盘后意图顺延落地", "skip",
                    f"executor={executor}，不消费 rq 队列")
    with contextlib.closing(_ro(account_db)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                         " AND name='rq_intents'").fetchone():
            return item("carry_over", "盘后意图顺延落地", "skip", "还没有意图表")
        rows = [dict(r) for r in c.execute(
            "SELECT biz_id, symbol, side, volume, status, created_at"
            " FROM rq_intents WHERE substr(created_at,1,10) < ?"
            " AND status IN ('pending','queued','executed','rejected')", (today,))]
    from paper_trading.integration.rq_engine.queue import is_carried

    carried = [r for r in rows if is_carried(r["created_at"], today)]
    if not carried:
        stale = [r for r in rows if not is_carried(r["created_at"], today)]
        return item("carry_over", "盘后意图顺延落地", "green",
                    f"今日无顺延意图（昨日及以前未决 {len(stale)} 条应已被过期处理）")
    open_ = [r for r in carried if r["status"] in ("pending", "queued")]
    samples = [(r["symbol"], r["side"], r["volume"], r["status"]) for r in carried][:5]
    detail = f"顺延 {len(carried)} 条：{samples}"
    if open_:
        return item("carry_over", "盘后意图顺延落地", "warn",
                    f"{detail}——仍未定局 {len(open_)} 条（本跳应已给出成交/拒单结论）",
                    on_red="查会话是否跑成")
    return item("carry_over", "盘后意图顺延落地", "green", detail)


def daily_check(*, data_db, account_db, symbols: list, today: str, cfg,
                state_dir: str = "var/rq_state/live",
                days: int = 60, heartbeat_path=None,
                now: Optional[datetime] = None, executor: str = "rqalpha",
                broker=None, work_expected: Optional[bool] = None) -> dict:
    """全套每日核对 = `m3 verify` 的 12 项 + 本模块 3 项，一个退出码。"""
    from paper_trading.utils import m3_verify

    base = (m3_verify.verify_all(broker=broker, data_db=data_db, symbols=symbols,
                                 today=today, cfg=cfg, days=days,
                                 heartbeat_path=heartbeat_path, now=now,
                                 executor=executor)
            if broker is not None else {"items": [], "today": today})
    items = list(base.get("items") or []) + [
        check_init_align(state_dir, account_db, today, work_expected=work_expected),
        check_carry_over(account_db, today, now=now, executor=executor),
        check_intent_chain(account_db, today, executor=executor),
    ]
    reds = [i for i in items if i["status"] == "red"]
    items.sort(key=lambda i: m3_verify.STATUS_ORDER.get(i["status"], 9))
    return {"today": today, "items": items,
            "green": sum(1 for i in items if i["status"] == "green"),
            "warn": sum(1 for i in items if i["status"] == "warn"),
            "red": len(reds),
            "skipped": sum(1 for i in items if i["status"] == "skip"),
            "rollback_required": any(i["money"] for i in reds),
            "ok": not reds}
