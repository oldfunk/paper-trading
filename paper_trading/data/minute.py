"""1 分钟 K 线采集（盘中量化用）：akshare→东方财富。

- 标准库+akshare，无需新依赖；失败返回空列表（永不抛错阻断主流程）
- 仅最近若干交易日可取（东财 1 分钟接口不提供远期历史）
- 拉取需礼貌节流：单只约 0.5-1s，连续多只按 GAP 秒间隔，避免被限流
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

from paper_trading.utils import get_logger

logger = get_logger(__name__)

GAP_SEC = 1.0          # 连续拉取间隔（东财限流较敏感）
MAX_DAYS = 5           # 东财 1 分钟最多近 5 个交易日


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _f(x, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def _rows_to_minutes(df, symbol: str) -> list[dict]:
    """东财返回列：时间/开盘/收盘/最高/最低/成交量/成交额/均价 → 内部 dict。"""
    if df is None or len(df) == 0:
        return []
    out: list[dict] = []
    for _, r in df.iterrows():
        ts = str(r.get("时间", "")).strip()
        if not ts:
            continue
        ts = ts.replace(" ", "T")[:16]          # 2026-10-19 14:43:00 → ...T14:43
        out.append({"ts": ts,
                    "open": _f(r.get("开盘")), "close": _f(r.get("收盘")),
                    "high": _f(r.get("最高")), "low": _f(r.get("最低")),
                    "volume": _f(r.get("成交量")), "amount": _f(r.get("成交额"))})
    out.sort(key=lambda x: x["ts"])
    return out


def fetch_minute(symbol: str, period: str = "1", days: int = MAX_DAYS,
                 now: Optional[datetime] = None) -> list[dict]:
    """拉单只最近 N 日的分钟线；失败返回 []。"""
    try:
        import akshare as ak
    except Exception as e:      # noqa: BLE001
        logger.warning(f"akshare unavailable for minute bars: {e}")
        return []
    now = now or datetime.now()
    days = max(1, min(int(days), MAX_DAYS))
    start = now - timedelta(days=days + 1)
    try:
        df = ak.stock_zh_a_hist_min_em(symbol=str(symbol), period=str(period),
                                       start_date=_fmt(start), end_date=_fmt(now),
                                       adjust="")
    except Exception as e:      # noqa: BLE001 - 网络/限流都退化为空
        logger.warning(f"minute fetch failed for {symbol}: {type(e).__name__}: {e}")
        return []
    rows = _rows_to_minutes(df, str(symbol))
    logger.info(f"Fetched {len(rows)} {period}m bars for {symbol}")
    return rows


def fetch_minute_batch(symbols: list[str], period: str = "1",
                       days: int = MAX_DAYS, gap: float = GAP_SEC,
                       now: Optional[datetime] = None) -> dict[str, int]:
    """批量拉分钟线（节流），返回 {symbol: 行数}。"""
    out: dict[str, int] = {}
    for i, sym in enumerate(symbols or []):
        rows = fetch_minute(sym, period=period, days=days, now=now)
        out[sym] = len(rows)
        if i + 1 < len(symbols) and gap > 0:
            time.sleep(gap)
    return out