"""原始价（不复权）日线管道：采集 → 独立源对照 → 落库 → 新鲜度闸。

T31（M2 数据侧，P0）。为什么需要这个模块：`daily_bars` 一直是**前复权**
（`akshare_fetcher.py` 默认 `adjust="qfq"`），而 rqalpha 的 DayBarStore 契约要
**原始价**，它自己会再按 `ex_cum_factor` 复权一次（`base_data_source/adjust.py`）。
把 qfq 当 raw 喂 = 双重计入 = D3 裁决否掉的 route A（实测 2,802.42 元），
量级见 `docs/worker/report-t13.md` §4.1：27,420 个可比标量-日里 84.3% 偏差 >0.5%。

四条硬规矩（AGENTS 纪律 3/4 + G-T13-1/2；§5.5 裁决 2 = A+B 同做）：
1. **口径标签**：写进 `daily_bars_raw` 的行必须 `adjust='raw'`，否则 `upsert_raw_bars` 拒写；
2. **独立对照（历史）**：每行与母库 `close_raw` 同日比对，超容差 → **整只拒收**（不静默降级）；
2b. **独立对照（当日，A）**：母库 `close_raw` 实测在 T 日为 NULL（2026-10-28 已到该日
   的 20 只全 NULL），故收盘后改用**腾讯实时快照**对照当日那根（`intraday_reference`）；
2c. **T+1 必须翻转（B）**：当日允许 `checked=0`，但母库补上后必须在下个交易日翻成
   已验证，否则 `raw_flip_watch()` 报 `stop_line`；
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
                   tolerance: float = RAW_CLOSE_TOLERANCE, *,
                   intraday: Optional[dict] = None, day: Optional[str] = None,
                   enforce_intraday: bool = False) -> dict:
    """把待写入的 raw bars 与**独立源**同日对照；返回裁决。

    `reference` = 母库 `{date: {"close_raw": x, ...}}`（历史主参照）。
    `intraday`   = `{price, date}` 腾讯实时快照（**当日**唯一可用的独立参照——
    母库 `close_raw` 实测在 T 日为 NULL：2026-10-28 已到该日的 20 只全是 NULL）。
    `enforce_intraday=True`（收盘后同步当日）时，缺参照或偏差 > 容差都算分歧：
    缺参照 → `no_intraday` 计数并判 `ok=False`？——**不**，缺参照不判死，
    由 `raw_flip_watch()` 在 T+1 未翻转时停线（§5.5 裁决 2 的 A+B 组合）。
    """
    ref = reference or {}
    mismatches: list[dict] = []
    verified: set = set()
    no_ref = 0
    no_intraday = 0
    intraday_checked = 0
    absurd: list[dict] = []
    for b in bars:
        if not (0.0 < float(b.close) <= RAW_ABS_MAX):
            absurd.append({"date": _as_date(b.timestamp).isoformat(),
                           "close": b.close, "why": "abs-price"})
            continue
        d = _as_date(b.timestamp).isoformat()
        if enforce_intraday and day and d == day:
            px = float((intraday or {}).get("price") or 0.0)
            if px <= 0 or (intraday or {}).get("date") != day:
                no_intraday += 1
            else:
                ratio_i = float(b.close) / px
                if abs(ratio_i - 1.0) > tolerance:
                    mismatches.append({"date": d, "akshare_close": round(float(b.close), 4),
                                       "intraday_price": round(px, 4),
                                       "ratio": round(ratio_i, 4), "why": "intraday"})
                else:
                    intraday_checked += 1
                    verified.add(d)
            continue
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
            "mismatches": mismatches, "absurd": absurd,
            "no_intraday": no_intraday, "intraday_checked": intraday_checked}


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
            "checked": verdict["checked"], "no_reference": verdict["no_reference"],
            "intraday_checked": verdict.get("intraday_checked", 0),
            "no_intraday": verdict.get("no_intraday", 0)}


def session_closed(now: Optional[datetime] = None, *, grace_min: int = 5) -> bool:
    """收盘判定：A 股 15:00 收盘 + 缓冲，**按北京时间算**。

    生产机可能是 UTC/其它时区（实测 NEC 跑批时本地钟还没到 15:05，
    用机器本地时间会漏掉当日跨源对照），所以：
    - **不传 `now`** ⇒ 取 UTC 现在再折算北京时间（与时区无关，这是生产路径）；
    - 传**无时区**的 `now` ⇒ 视为北京时间墙钟（测试/手工指定用）；
    - 传**带时区**的 `now` ⇒ 折算到北京时间。
    """
    from datetime import timezone

    CN = timezone(timedelta(hours=8))
    if now is None:
        cn = datetime.now(timezone.utc).astimezone(CN)
    elif now.tzinfo is None:
        cn = now
    else:
        cn = now.astimezone(CN)
    return cn.hour * 60 + cn.minute >= 15 * 60 + grace_min


def intraday_reference(symbols, *, quotes: Optional[dict] = None,
                       get_quotes=None) -> dict:
    """当日独立参照：腾讯实时快照（与 akshare/新浪**不同源**，符合独立基准原则）。

    拿不到就返回 {}——当日缺参照不判死，由 `raw_flip_watch()` 负责 T+1 停线。
    """
    if quotes is None:
        fn = get_quotes
        if fn is None:
            try:
                from paper_trading.data.realtime import get_quotes as fn
            except Exception:               # noqa: BLE001 - 无网络/依赖缺失
                return {}
        try:
            quotes = fn([str(s) for s in symbols]) or {}
        except Exception:                   # noqa: BLE001
            return {}
    out = {}
    for code, q in (quotes or {}).items():
        sym = str(code).strip()[-6:].zfill(6)
        try:
            px = float((q or {}).get("price") or 0.0)
        except (TypeError, ValueError):
            px = 0.0
        if px > 0:
            out[sym] = {"price": px, "date": str((q or {}).get("date") or "")}
    return out


def raw_flip_watch(db, symbols, today: Optional[date] = None,
                   max_lag: int = MAX_LAG_TRADING_DAYS,
                   ref_asof: Optional[dict] = None) -> dict:
    """G-T13-1 增补判据（§5.5 裁决 2-B）：**当日允许 checked=0，T+1 必须翻转，否则停线**。

    三种未翻转要分开对待，否则"验料源自己滞后"会永远停我们的线：
    - `waiting_reference`：母库连参照都还没给到该日（`ref_asof` 显示滞后）⇒ **告警不停线**；
    - `checked_not_flipped`：参照已有、我们没重跑对照 ⇒ **停线**（我们的锅）；
    - `raw_stale` / `no_raw_data`：喂料本身断了 ⇒ **停线**。
    停线动作由调用方（引擎/看门狗）执行；本函数只给可审计结论。
    """
    today_d = _as_date(today or date.today())
    ref_asof = ref_asof or {}
    out = {}
    for sym in symbols:
        last_bar = db.raw_latest(sym)
        last_ck = db.raw_latest_checked(sym)
        if not last_bar:
            out[sym] = {"flip_ok": False, "stop": True, "reason": "no_raw_data",
                        "bar_last": None, "checked_last": None, "checked_lag": None}
            continue
        fresh = raw_freshness(db, [sym], today=today_d, max_lag=max_lag)[sym]
        ck_lag = None
        if last_ck:
            from paper_trading.utils.trading_calendar import is_trading_day
            d, n = _as_date(last_ck), 0
            while d < today_d and n < 400:
                d += timedelta(days=1)
                if is_trading_day(d):
                    n += 1
            ck_lag = n
        bar_last = fresh.get("last")
        ref_day = str(ref_asof.get(sym) or "")[:10]
        waiting = bool(ref_day) and bool(bar_last) and bar_last > ref_day
        if fresh.get("stale"):
            reason, stop = "raw_stale", True
        elif waiting and (ck_lag is None or ck_lag > max_lag):
            reason, stop = "waiting_reference", False        # 等母库，不是我们的锅
        elif ck_lag is None or ck_lag > max_lag:
            reason, stop = "checked_not_flipped", True       # 参照已有却没重跑
        else:
            reason, stop = None, False
        out[sym] = {"flip_ok": not stop, "stop": stop, "reason": reason,
                    "bar_last": bar_last, "checked_last": str(last_ck)[:10] if last_ck else None,
                    "checked_lag": ck_lag, "ref_asof": ref_day or None}
    return {"today": today_d.isoformat(), "max_lag_trading_days": max_lag,
            "symbols": len(out),
            "violations": sorted(s for s, v in out.items() if v["stop"]),
            "warnings": sorted(s for s, v in out.items() if v["reason"] == "waiting_reference"),
            "stop_line": any(v["stop"] for v in out.values()), "per_symbol": out}


def sync_raw(db, symbols, *, days: int = 30,
             fetch: Optional[Callable[[str, int], list[Bar]]] = None,
             reference: Optional[Callable[[list[str], int], dict]] = None,
             today: Optional[date] = None, now: Optional[datetime] = None,
             intraday: Optional[dict] = None,
             quotes: Optional[dict] = None) -> dict:
    """批量同步。`fetch`/`reference`/`intraday`/`quotes` 可注入（测试零网络）。"""
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

    intraday_map: dict = {}
    day = (today or date.today()).isoformat()
    enforce = session_closed(now or datetime.now())
    if enforce:
        intraday_map = (intraday if intraday is not None
                        else intraday_reference(symbols, quotes=quotes))

    results = []
    no_today_bar = 0
    for sym in symbols:
        try:
            bars = fetch(sym, days) or []
        except Exception as exc:              # noqa: BLE001 - 单只失败不影响整批
            results.append({"symbol": sym, "written": 0, "status": "fetch_error",
                            "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
            continue
        if enforce and not any(_as_date(b.timestamp).isoformat() == day for b in bars):
            # 源还没出今天那根日K（新浪收盘后延迟发布）⇒ 跨源对照"没做"必须可见，
            # 不能报成 0/0 让人以为已经校过
            no_today_bar += 1
        verdict = check_raw_bars(bars, refs.get(sym) or {}, day=day,
                                 intraday=intraday_map.get(sym),
                                 enforce_intraday=enforce)
        results.append(sync_symbol(sym, bars, verdict, db))
    written = sum(r["written"] for r in results)
    ref_asof = {s: (max(v) if v else "") for s, v in (refs or {}).items()}
    flip = raw_flip_watch(db, symbols, today=today or date.today(), ref_asof=ref_asof)
    return {"today": day, "requested": len(symbols),
            "written": written, "results": results,
            "intraday_enforced": enforce,
            "intraday_missing": sum(r.get("no_intraday") or 0 for r in results),
            "intraday_checked": sum(r.get("intraday_checked") or 0 for r in results),
            "intraday_no_today_bar": no_today_bar,
            "flip_watch": {k: v for k, v in flip.items() if k != "per_symbol"},
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
