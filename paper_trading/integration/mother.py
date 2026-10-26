"""母库只读富化：**每个接口独立降级**，不做整体切换。

设计要点：

1. **母库优先**：母库有该接口的数据就用母库（``source="mother:<table>"``）。
2. **逐接口回退**：母库没有该标的/该表不可用时，只回退该接口的本地来源；
   本地也没有则返回 ``source="unavailable"``（``data=None``），**不造假**。
3. **性能友好**：只读（``mode=ro`` + ``query_only``）、按 code 精确查询
   （母库已有索引）、内存 TTL 缓存、短超时。母库正在回填时不会被拖慢。
4. **来源标注**：所有返回值经 :func:`paper_trading.integration.sources.block`
   包装，带 ``source/asof/stale/available``。

注意（调整价口径）：母库 ``kline_daily`` 提供不复权/后复权，子项目自有日线是
前复权（akshare ``qfq``）。两者不可直接混用于交易信号，因此 ``daily_bars``
返回 ``adj`` 标注；执行路径仍以前复权本地数据为准（见 ``docs/mother-interface.md``）。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .settings import mother_db_path
from .sources import (SRC_LOCAL_AKSHARE, SRC_UNAVAILABLE, block, mother_source,
                      unavailable)

#: 默认缓存 TTL（秒）。日线盘中会变，取短；基本面/筛选取长。
TTL_QUOTES = 60.0
TTL_FUNDAMENTAL = 300.0
TTL_SCREENING = 600.0

_CACHE: dict = {}
_COLUMNS: dict = {}


def reset_cache() -> None:
    """清空内存缓存（测试/运维用）。"""
    _CACHE.clear()
    _COLUMNS.clear()


def _conn(mother: Optional[Path] = None):
    """只读连接母库；不可用返回 None。"""
    db = mother_db_path(mother)
    if db is None:
        return None
    try:
        conn = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=2000")
        _COLUMNS.clear()      # 新连接可能指向不同库：避免复用旧列缓存
        return conn
    except Exception:                       # noqa: BLE001 - 母库不可用即降级
        return None


def _columns(conn, table: str) -> set:
    """表的列名集合（带缓存）；表不存在返回空集。"""
    key = (str(id(conn)), table)
    if key in _COLUMNS:
        return _COLUMNS[key]
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        cols = {str(r[1]) for r in rows}
    except Exception:                       # noqa: BLE001
        cols = set()
    _COLUMNS[key] = cols
    return cols


def _has(conn, table: str, required: Iterable[str]) -> bool:
    cols = _columns(conn, table)
    return bool(cols) and all(c in cols for c in required)


def _cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


def _as_date(value) -> str:
    s = str(value or "")[:10]
    return s


def _stale(asof: Optional[str], max_age_days: int, today: str) -> bool:
    if not asof:
        return False
    from datetime import date
    try:
        d = date.fromisoformat(asof)
        t = date.fromisoformat(today)
        return (t - d).days > max_age_days
    except ValueError:
        return False


# ---------- 日线：母库 kline_daily → 本地 akshare ----------

def daily_bars(codes: Iterable[str], days: int = 250,
               local_bars: Optional[Callable[[str, int], list]] = None,
               mother: Optional[Path] = None,
               ttl: float = TTL_QUOTES) -> dict:
    """每只标的的日线（母库优先，缺该标的回退本地）。

    ``local_bars(code, days)`` 由调用方注入（返回本地 Bar 列表或字典列表）。
    返回 ``{code: {"available","source","asof","adj","bars":[...]}}``。
    """
    out: dict = {}
    codes = [str(c).strip().zfill(6) for c in (codes or [])]
    conn = _conn(mother)
    try:
        for code in dict.fromkeys(codes):
            bars: list = []
            source = SRC_UNAVAILABLE
            asof = None
            adj = None
            if conn is not None and _has(conn, "kline_daily",
                                         ("code", "trade_date", "close")):
                def _fetch():
                    rows = conn.execute(
                        """SELECT trade_date, open, high, low, close,
                                  volume, amount, close_adj
                           FROM kline_daily WHERE code = ?
                           ORDER BY trade_date DESC LIMIT ?""",
                        (code, int(days))).fetchall()
                    return [dict(r) for r in rows]

                try:
                    rows = _cached(f"kline:{id(conn)}:{code}:{days}", ttl, _fetch)
                except Exception:           # noqa: BLE001 - 单只失败不影响其他
                    rows = []
                if rows:
                    bars = [{
                        "date": _as_date(r["trade_date"]),
                        "open": r["open"], "high": r["high"],
                        "low": r["low"], "close": r["close_adj"] or r["close"],
                        "volume": r["volume"],
                        "amount": r.get("amount"),
                    } for r in rows]
                    source = mother_source("kline_daily")
                    asof = _as_date(rows[0]["trade_date"])
                    adj = "hfq" if rows[0].get("close_adj") else "raw"
            if not bars and local_bars is not None:
                try:
                    local = local_bars(code, days) or []
                except Exception:           # noqa: BLE001
                    local = []
                if local:
                    norm = []
                    for b in local:
                        if isinstance(b, dict):
                            ts = str(b.get("timestamp") or b.get("date") or "")
                            norm.append({
                                "date": _as_date(ts),
                                "open": b.get("open"), "high": b.get("high"),
                                "low": b.get("low"), "close": b.get("close"),
                                "volume": b.get("volume"),
                                "amount": b.get("amount"),
                            })
                        else:
                            ts = str(getattr(b, "timestamp", "") or "")
                            norm.append({
                                "date": _as_date(ts),
                                "open": getattr(b, "open", None),
                                "high": getattr(b, "high", None),
                                "low": getattr(b, "low", None),
                                "close": getattr(b, "close", None),
                                "volume": getattr(b, "volume", None),
                                "amount": None,
                            })
                    bars = norm
                    source = SRC_LOCAL_AKSHARE
                    asof = bars[0]["date"] if bars else None
                    adj = "qfq"
            out[code] = {
                "available": bool(bars), "source": source, "asof": asof,
                "adj": adj, "bars": bars,
            }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:               # noqa: BLE001
                pass
    return out


# ---------- 基本面 / 财务 / 股息 / 基准 / 新闻 / 筛选 / 论点 / 追踪 ----------

def financial_summary(codes: Iterable[str], mother: Optional[Path] = None,
                      ttl: float = TTL_FUNDAMENTAL) -> dict:
    """母库财务摘要（ROE 5y/10y、毛利/净利率、OCF、负债、ROIC）。无本地等价 → unavailable。"""
    codes = [str(c).strip().zfill(6) for c in (codes or [])]
    if not codes:
        return {}
    conn = _conn(mother)
    if conn is None or not _has(conn, "financial_summary", ("stock_code",)):
        if conn is not None:
            conn.close()
        return {c: unavailable("母库无 financial_summary 或不可读") for c in codes}
    try:
        def _fetch():
            q = (",".join("?" * len(codes)))
            rows = conn.execute(
                f"SELECT * FROM financial_summary WHERE stock_code IN ({q})",
                codes).fetchall()
            return [dict(r) for r in rows]

        try:
            rows = _cached(f"fin:{id(conn)}:{','.join(codes)}", ttl, _fetch)
        except Exception:                   # noqa: BLE001
            rows = []
        out = {c: unavailable("母库未覆盖该标的财务摘要") for c in codes}
        for r in rows:
            c = str(r["stock_code"]).zfill(6)
            out[c] = block(r, mother_source("financial_summary"),
                           asof=_as_date(r.get("updated_at")))
        return out
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass


def dividend(codes: Iterable[str], mother: Optional[Path] = None,
             ttl: float = TTL_FUNDAMENTAL) -> dict:
    """母库年度分红。无本地等价 → unavailable。"""
    codes = [str(c).strip().zfill(6) for c in (codes or [])]
    if not codes:
        return {}
    conn = _conn(mother)
    if conn is None or not _has(conn, "dividend_annual", ("stock_code",)):
        if conn is not None:
            conn.close()
        return {c: unavailable("母库无 dividend_annual 或不可读") for c in codes}
    try:
        q = ",".join("?" * len(codes))

        def _fetch():
            return [dict(r) for r in conn.execute(
                f"""SELECT stock_code, report_date, cash_per_share, eps,
                           reported_yield, source
                    FROM dividend_annual WHERE stock_code IN ({q})
                    ORDER BY stock_code, report_date DESC""", codes).fetchall()]

        try:
            rows = _cached(f"div:{id(conn)}:{','.join(codes)}", ttl, _fetch)
        except Exception:                   # noqa: BLE001
            rows = []
        grouped: dict = {}
        for r in rows:
            grouped.setdefault(str(r["stock_code"]).zfill(6), []).append(r)
        out = {c: unavailable("母库未覆盖该标的股息") for c in codes}
        for c, items in grouped.items():
            out[c] = block(items, mother_source("dividend_annual"),
                           asof=_as_date(items[0]["report_date"]))
        return out
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass


def benchmark(codes: Iterable[str], days: int = 60,
              mother: Optional[Path] = None,
              ttl: float = TTL_QUOTES) -> dict:
    """母库基准指数日线（相对强度用）。无本地等价 → unavailable。"""
    codes = [str(c).strip() for c in (codes or [])]
    if not codes:
        return {}
    conn = _conn(mother)
    if conn is None or not _has(conn, "benchmark_daily", ("code", "trade_date", "close")):
        if conn is not None:
            conn.close()
        return {c: unavailable("母库无 benchmark_daily 或不可读") for c in codes}
    try:
        q = ",".join("?" * len(codes))

        def _fetch():
            return [dict(r) for r in conn.execute(
                f"""SELECT code, trade_date, close FROM benchmark_daily
                    WHERE code IN ({q}) ORDER BY code, trade_date DESC""",
                codes).fetchall()]

        try:
            rows = _cached(f"bench:{id(conn)}:{','.join(codes)}:{days}", ttl, _fetch)
        except Exception:                   # noqa: BLE001
            rows = []
        grouped: dict = {}
        for r in rows:
            grouped.setdefault(str(r["code"]), []).append(r)
        out = {c: unavailable("母库未覆盖该基准") for c in codes}
        for c, items in grouped.items():
            items = items[:int(days)]
            out[c] = block([{"date": _as_date(x["trade_date"]), "close": x["close"]}
                            for x in items], mother_source("benchmark_daily"),
                           asof=_as_date(items[0]["trade_date"]))
        return out
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass


def news(codes: Optional[Iterable[str]] = None, limit: int = 20,
         local_news: Optional[Callable[[], dict]] = None,
         mother: Optional[Path] = None,
         ttl: float = TTL_FUNDAMENTAL) -> dict:
    """新闻：母库 ``news_item`` 优先（个股 + 全局），缺则回退本地 akshare 快讯。"""
    codes = [str(c).strip().zfill(6) for c in (codes or [])]
    conn = _conn(mother)
    if conn is not None and _has(conn, "news_item", ("code", "title")):
        try:
            def _fetch():
                rows = []
                if codes:
                    q = ",".join("?" * len(codes))
                    rows += [dict(r) for r in conn.execute(
                        f"""SELECT code, name, title, summary, source, published_at,
                                   sentiment, ai_brief
                            FROM news_item WHERE code IN ({q})
                            ORDER BY published_at DESC LIMIT ?""",
                        codes + [int(limit)]).fetchall()]
                rows += [dict(r) for r in conn.execute(
                    """SELECT code, name, title, summary, source, published_at,
                              sentiment, ai_brief
                       FROM news_item WHERE code = '' ORDER BY published_at DESC LIMIT ?""",
                    (int(limit),)).fetchall()]
                return rows

            try:
                rows = _cached(f"news:{id(conn)}:{','.join(codes)}:{limit}", ttl, _fetch)
            except Exception:               # noqa: BLE001
                rows = []
            if rows:
                asof = _as_date(max(str(r.get("published_at") or "") for r in rows))
                return block(rows, mother_source("news_item"), asof=asof)
        finally:
            try:
                conn.close()
            except Exception:               # noqa: BLE001
                pass
    # 回退本地 akshare 快讯（调用方注入，避免 integration 依赖 data.news）
    if local_news is not None:
        try:
            n = local_news() or {}
        except Exception:                   # noqa: BLE001
            n = {}
        if n.get("available"):
            return block(n.get("items"), SRC_LOCAL_AKSHARE,
                         asof=n.get("fetched_at") or None)
    return unavailable("母库与本地均无新闻")


def screening_detail(local_pool: Optional[list] = None,
                     mother: Optional[Path] = None,
                     ttl: float = TTL_SCREENING) -> dict:
    """母库最新一轮筛选明细（含 score_detail/reason/strategy_tags）。

    母库不可用/无结果时回退本地手填池（无评分，仅代码列表）。
    """
    conn = _conn(mother)
    if conn is not None and _has(conn, "screening_result", ("run_id", "run_date")):
        try:
            def _fetch():
                run = conn.execute(
                    "SELECT run_id, run_date FROM screening_result "
                    "GROUP BY run_id ORDER BY run_date DESC, run_id DESC LIMIT 1"
                ).fetchone()
                if not run:
                    return None
                rows = [dict(r) for r in conn.execute(
                    """SELECT code, name, score, pe, pb, roe, reason, strategy_tags,
                              dividend_yield, score_detail
                       FROM screening_result WHERE run_id = ?
                       ORDER BY score DESC""", (run["run_id"],)).fetchall()]
                return {"run_id": run["run_id"], "run_date": _as_date(run["run_date"]),
                        "rows": rows}

            try:
                run = _cached(f"screening:latest:{id(conn)}", ttl, _fetch)
            except Exception:               # noqa: BLE001
                run = None
            if run and run.get("rows"):
                return block(run, mother_source("screening_result"),
                             asof=run.get("run_date"))
        finally:
            try:
                conn.close()
            except Exception:               # noqa: BLE001
                pass
    if local_pool:
        return block({"run_id": None, "run_date": None,
                      "rows": [{"code": str(c).zfill(6)} for c in local_pool]},
                     "local:config", asof=None)
    return unavailable("母库无筛选结果且本地无手填池")


def thesis(codes: Iterable[str], mother: Optional[Path] = None,
           ttl: float = TTL_FUNDAMENTAL) -> dict:
    """母库观察池论点（core_thesis / sell_conditions）。无本地等价 → unavailable。"""
    codes = [str(c).strip().zfill(6) for c in (codes or [])]
    if not codes:
        return {}
    conn = _conn(mother)
    if conn is None or not _has(conn, "watchlist_thesis", ("code",)):
        if conn is not None:
            conn.close()
        return {c: unavailable("母库无 watchlist_thesis 或不可读") for c in codes}
    try:
        q = ",".join("?" * len(codes))

        def _fetch():
            return [dict(r) for r in conn.execute(
                f"""SELECT code, core_thesis, assumptions, red_lines, sell_conditions
                    FROM watchlist_thesis WHERE code IN ({q})""", codes).fetchall()]

        try:
            rows = _cached(f"thesis:{id(conn)}:{','.join(codes)}", ttl, _fetch)
        except Exception:                   # noqa: BLE001
            rows = []
        out = {c: unavailable("母库未覆盖该标的论点") for c in codes}
        for r in rows:
            c = str(r["code"]).zfill(6)
            out[c] = block(r, mother_source("watchlist_thesis"))
        return out
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass


def outcomes(codes: Iterable[str], mother: Optional[Path] = None,
             ttl: float = TTL_FUNDAMENTAL) -> dict:
    """母库 AI 结论追踪（analysis_outcome）。覆盖度低时明确提示，不阻断。"""
    codes = [str(c).strip().zfill(6) for c in (codes or [])]
    if not codes:
        return {}
    conn = _conn(mother)
    if conn is None or not _has(conn, "analysis_outcome", ("stock_code",)):
        if conn is not None:
            conn.close()
        return {c: unavailable("母库无 analysis_outcome 或不可读") for c in codes}
    try:
        q = ",".join("?" * len(codes))

        def _fetch():
            return [dict(r) for r in conn.execute(
                f"""SELECT stock_code, analysis_date, signal, confidence, verdict,
                           entry_price, ret_20, ret_60, excess_20, excess_60
                    FROM analysis_outcome WHERE stock_code IN ({q})
                    ORDER BY analysis_date DESC""", codes).fetchall()]

        try:
            rows = _cached(f"outcome:{id(conn)}:{','.join(codes)}", ttl, _fetch)
        except Exception:                   # noqa: BLE001
            rows = []
        grouped: dict = {}
        for r in rows:
            grouped.setdefault(str(r["stock_code"]).zfill(6), []).append(r)
        out = {}
        for c in codes:
            items = grouped.get(c) or []
            if not items:
                out[c] = unavailable("母库未覆盖该标的的 AI 追踪")
                continue
            judged = [x for x in items if str(x.get("verdict") or "").strip()]
            out[c] = block(items, mother_source("analysis_outcome"),
                           asof=_as_date(items[0]["analysis_date"]),
                           note=f"{len(judged)}/{len(items)} 条有 verdict"
                                if judged else "verdict 未落全（母库回填中）")
        return out
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass


def regime(mother: Optional[Path] = None,
           ttl: float = TTL_QUOTES) -> dict:
    """母库大盘状态（指数最新值/涨跌幅/PE/PB）。无本地等价 → unavailable。"""
    conn = _conn(mother)
    if conn is None or not _has(conn, "market_index", ("index_code", "date")):
        if conn is not None:
            conn.close()
        return unavailable("母库无 market_index 或不可读")
    try:
        def _fetch():
            rows = []
            for code in ("000001", "399001", "399006"):
                r = conn.execute(
                    """SELECT index_code, index_name, current_value, change_percent,
                              pe, pb, date FROM market_index WHERE index_code = ?
                       ORDER BY date DESC LIMIT 1""", (code,)).fetchone()
                if r:
                    rows.append(dict(r))
            return rows

        try:
            rows = _cached(f"regime:{id(conn)}", ttl, _fetch)
        except Exception:                   # noqa: BLE001
            rows = []
        if not rows:
            return unavailable("母库大盘数据为空")
        asof = max(_as_date(r.get("date")) for r in rows if r.get("date"))
        return block(rows, mother_source("market_index"), asof=asof)
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass
