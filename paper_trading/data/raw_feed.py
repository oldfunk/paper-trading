"""原始价（不复权）日线管道：采集 → 独立源对照 → 落库 → 新鲜度闸。

T31（M2 数据侧，P0）。为什么需要这个模块：`daily_bars` 一直是**前复权**
（`akshare_fetcher.py` 默认 `adjust="qfq"`），而 rqalpha 的 DayBarStore 契约要
**原始价**，它自己会再按 `ex_cum_factor` 复权一次（`base_data_source/adjust.py`）。
把 qfq 当 raw 喂 = 双重计入 = D3 裁决否掉的 route A（实测 2,802.42 元），
量级见 `docs/worker/report-t13.md` §4.1：27,420 个可比标量-日里 84.3% 偏差 >0.5%。

三条硬规矩（AGENTS 纪律 3/4 + G-T13-1/2）：
1. **口径标签**：写进 `daily_bars_raw` 的行必须 `adjust='raw'`，否则 `upsert_raw_bars` 拒写；
2. **独立对照**：每行与母库 `close_raw` 同日比对，超容差 → **整只拒收**（不静默降级）；
3. **新鲜度**：喂引擎前先问 `raw_freshness()`——缺最新交易日就是缺数据，
   引擎按"缺数据不下单"处理，而不是拿旧价凑数。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable, Optional

from paper_trading.models import Bar
from paper_trading.utils import get_logger

logger = get_logger(__name__)

#: raw 对 raw 的同日收盘容差。两条独立源实测**逐分一致**
#: （000792 2026-10-26：akshare 22.24 / 母库 close_raw 22.24，volume/amount 也相同），
#: 所以这里可以比 qfq 跨源闸（10%）严得多；0.5% 仍远高于 0.01 元的最小变动位，
#: 而尺度污染事故的那类错误是 6.4×，一律落网。
RAW_CLOSE_TOLERANCE = 0.005
#: 绝对量级闸（沿用 H-7 思路：A 股真实股价 <10 万元/股）
RAW_ABS_MAX = 100_000.0
#: 允许的最大滞后（交易日数）：>此值即视为陈旧
MAX_LAG_TRADING_DAYS = 1


def _as_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v)[:10]).date()


def check_raw_bars(bars: list[Bar], reference: Optional[dict] = None,
                   tolerance: float = RAW_CLOSE_TOLERANCE) -> dict:
    """把待写入的 raw bars 与母库 `close_raw` 同日对照；返回裁决。

    `reference` = `{date: {"close_raw": x, "volume": y, "amount": z}}`（可为 None，
    表示母库缺该日 —— 缺参照不等于有错，但会被计数，供 provenance 用）。
    """
    ref = reference or {}
    mismatches: list[dict] = []
    verified: set = set()
    no_ref = 0
    absurd: list[dict] = []
    for b in bars:
        if not (0.0 < float(b.close) <= RAW_ABS_MAX):
            absurd.append({"date": _as_date(b.timestamp).isoformat(),
                           "close": b.close, "why": "abs-price"})
            continue
        d = _as_date(b.timestamp).isoformat()
        r = ref.get(d)
        if not r or not r.get("close_raw"):
            no_ref += 1
            continue
        try:
            ratio = float(b.close) / float(r["close_raw"])
        except (TypeError, ValueError, ZeroDivisionError):
            ratio = float("nan")
        if abs(ratio - 1.0) > tolerance:
            mismatches.append({"date": d,
                               "akshare_close": round(float(b.close), 4),
                               "mother_close_raw": round(float(r["close_raw"]), 4),
                               "ratio": round(ratio, 4)})
        else:
            verified.add(d)
    return {"ok": not mismatches and not absurd,
            "bars": len(bars), "no_reference": no_ref,
            "checked": len(verified), "verified_dates": verified,
            "mismatches": mismatches, "absurd": absurd}


def sync_symbol(symbol: str, bars: list[Bar], verdict: dict, db,
                source: str = "akshare:sina:raw") -> dict:
    """按裁决落库：干净才写（对照过的那批 checked=1），有分歧整只拒收。"""
    if not bars:
        return {"symbol": symbol, "written": 0, "status": "empty"}
    if not verdict["ok"]:
        logger.warning("raw 口径分歧，拒收 %s：%s", symbol, verdict["mismatches"][:3])
        return {"symbol": symbol, "written": 0, "status": "rejected",
                "mismatches": verdict["mismatches"], "absurd": verdict["absurd"]}
    n = db.upsert_raw_bars(bars, source=source,
                           checked_dates=verdict["verified_dates"])
    return {"symbol": symbol, "written": n, "status": "ok",
            "checked": verdict["checked"], "no_reference": verdict["no_reference"]}


def sync_raw(db, symbols, *, days: int = 30,
             fetch: Optional[Callable[[str, int], list[Bar]]] = None,
             reference: Optional[Callable[[list[str], int], dict]] = None,
             today: Optional[date] = None) -> dict:
    """批量同步。`fetch`/`reference` 可注入（测试用假源，不打网络）。"""
    if fetch is None:
        from paper_trading.data.akshare_fetcher import AkshareFetcher

        def fetch(sym, d):
            start = (date.today() - timedelta(days=int(d) * 2 + 10)).strftime("%Y%m%d")
            return AkshareFetcher.fetch_daily_raw(sym, start_date=start)
    symbols = [str(s).strip().zfill(6) for s in (symbols or [])]
    refs: dict = {}
    if reference is None:
        try:
            from paper_trading.integration import mother as _m
            refs = {c: _m.raw_close_map([c], days=days).get(c, {}) for c in symbols}
        except Exception as exc:              # noqa: BLE001 - 母库不可用则全部走单源
            logger.warning("母库 raw 参照不可用（%s），本轮单源写入 checked=0", exc)
            refs = {c: {} for c in symbols}
    else:
        refs = reference(symbols, days) or {}

    results = []
    for sym in symbols:
        try:
            bars = fetch(sym, days) or []
        except Exception as exc:              # noqa: BLE001 - 单只失败不影响整批
            results.append({"symbol": sym, "written": 0, "status": "fetch_error",
                            "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
            continue
        verdict = check_raw_bars(bars, refs.get(sym) or {})
        results.append(sync_symbol(sym, bars, verdict, db))
    written = sum(r["written"] for r in results)
    return {"today": (today or date.today()).isoformat(), "requested": len(symbols),
            "written": written, "results": results,
            "rejected": [r["symbol"] for r in results if r["status"] == "rejected"]}


def raw_freshness(db, symbols, today: Optional[date] = None,
                  max_lag: int = MAX_LAG_TRADING_DAYS) -> dict:
    """每只标的 raw 数据的陈旧度（G-T13-2 的本地半边）。

    落后 > `max_lag` 个交易日即 `stale=True`——引擎侧据此拒单，而不是拿旧 bar 凑。
    """
    from paper_trading.utils.trading_calendar import is_trading_day

    today = _as_date(today or date.today())
    out = {}
    for sym in symbols:
        last = db.raw_latest(sym)
        if not last:
            out[sym] = {"last": None, "lag": None, "stale": True, "reason": "no_raw_data"}
            continue
        last_d = _as_date(last)
        lag, d = 0, last_d
        while d < today:
            d += timedelta(days=1)
            if is_trading_day(d):
                lag += 1
        out[sym] = {"last": last_d.isoformat(), "lag": lag, "stale": lag > max_lag,
                    "last_checked": (db.raw_latest_checked(sym) or "")[:10] or None}
    return out


def factor_freshness_ok(today, factor_last, *, max_lag_days: int = 7) -> dict:
    """复权因子新鲜度裁决（G-T13-2 的回测线半边；因子末日读取由 T32 适配器负责）。

    因子比"今日"旧超过 `max_lag_days` 自然日 ⇒ 拒跑：期间若有除权除息，
    原始价换算就是**系统性错价**，外推不是降级（report-t13 §4.3）。
    """
    if factor_last is None:
        return {"ok": False, "reason": "factor_unavailable", "lag_days": None}
    fl, td = _as_date(factor_last), _as_date(today)
    lag = (td - fl).days
    return {"ok": lag <= max_lag_days, "factor_last": fl.isoformat(),
            "today": td.isoformat(), "lag_days": lag,
            "reason": None if lag <= max_lag_days else "factor_stale"}
