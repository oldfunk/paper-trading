"""前复权锚点/源切换检测（M1.5，只读）。

目的：前复权序列在除权除息后会整体重算锚点；如果本地与母库的锚点日不同，
两条 qfq 序列的**同日比值**会在某个日期跳变。这里只看比值是否稳定：
- 比值基本恒定（容差内）→ 同锚点/同步；
- 比值在某个日期跳变 → 一方刚重锚（可能源切换/回填滞后），列出来人工复核；
- 比值整体偏离 1 → 口径/尺度不一致（不是锚点漂移）。

不自动改数据、不改策略，只给 `data qfq-check` 与 `ops check` 用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional


def check(data_db, symbols: Iterable[str], *, days: int = 120,
          tol: float = 0.001, mother=None) -> dict:
    """返回每只标的的 qfq 比值稳定性。`tol` 为最大允许相对漂移（默认 0.1%）。"""
    from paper_trading.integration import mother as m

    syms = sorted({str(s).strip().zfill(6) for s in (symbols or []) if s})
    if not syms:
        return {"ok": True, "available": True, "symbols": [],
                "status": "skip", "items": [], "note": "股票池为空"}
    try:
        blocks = m.daily_bars(syms, days=int(days), local_bars=None,
                              mother=mother) or {}
    except Exception as exc:                # noqa: BLE001
        return {"ok": False, "available": False, "symbols": syms,
                "status": "unavailable",
                "reason": f"{type(exc).__name__}: {exc}", "items": []}
    items = []
    for sym in syms:
        blk = blocks.get(sym) or {}
        if not blk.get("available"):
            items.append({"symbol": sym, "status": "unavailable",
                          "reason": blk.get("note") or "母库无该标的日线"})
            continue
        if str(blk.get("adj") or "") not in ("", "qfq"):
            items.append({"symbol": sym, "status": "uncomparable",
                          "reason": f"母库口径为 {blk.get('adj')}，不是 qfq"})
            continue
        mrows = {str(r.get("date"))[:10]: float(r["close"])
                 for r in (blk.get("bars") or [])
                 if r.get("date") is not None and r.get("close") is not None}
        try:
            lbars = data_db.get_bars(sym, limit=int(days)) or []
        except Exception as exc:            # noqa: BLE001
            items.append({"symbol": sym, "status": "unavailable",
                          "reason": f"本地日线不可读：{type(exc).__name__}: {exc}"})
            continue
        lrows = {}
        for b in lbars:
            try:
                d = b.timestamp.date().isoformat()
                if b.adjust not in (None, "qfq"):
                    continue
                lrows[d] = float(b.close)
            except (TypeError, ValueError, AttributeError):
                continue
        common = sorted(set(mrows) & set(lrows))
        if len(common) < 10:
            items.append({"symbol": sym, "status": "insufficient",
                          "common_days": len(common),
                          "reason": f"共同交易日不足（{len(common)} < 10）"})
            continue
        ratios = [(d, lrows[d] / mrows[d]) for d in common
                  if mrows[d] > 0 and lrows[d] > 0]
        if len(ratios) < 10:
            items.append({"symbol": sym, "status": "insufficient",
                          "common_days": len(ratios),
                          "reason": f"有效比值不足（{len(ratios)} < 10）"})
            continue
        base = ratios[0][1]
        devs = [(d, abs(r / base - 1.0)) for d, r in ratios]
        worst_d, worst = max(devs, key=lambda x: x[1])
        base_dev = abs(base - 1.0)
        if base_dev > 0.10:
            status, reason = "scale_mismatch", (
                f"首日比值 {base:.4f} 偏离 1 超过 10%（口径/尺度不一致）")
        elif worst > float(tol):
            status, reason = "anchor_shift", (
                f"比值在 {worst_d} 漂移 {worst:.2%}（容差 {tol:.2%}）")
        else:
            status, reason = "ok", "比值稳定"
        items.append({
            "symbol": sym, "status": status,
            "common_days": len(ratios),
            "ratio_first": round(base, 6),
            "ratio_last": round(ratios[-1][1], 6),
            "max_dev": round(worst, 6),
            "max_dev_date": worst_d,
            "reason": reason,
        })
    bad = [x for x in items if x["status"] in ("anchor_shift", "scale_mismatch")]
    uncomparable = [x for x in items if x["status"] in
                    ("unavailable", "uncomparable", "insufficient")]
    return {
        "ok": not bad,
        "available": True,
        "symbols": syms,
        "status": "red" if bad else ("warn" if uncomparable else "green"),
        "items": items,
        "bad": [x["symbol"] for x in bad],
        "uncomparable": [x["symbol"] for x in uncomparable],
        "note": "只读锚点检测；anchor_shift 需人工核对除权日/源，不自动改数据",
    }
