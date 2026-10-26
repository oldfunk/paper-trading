"""行情数据源：母库优先 / 本地前复权（逐标的降级，带来源标注）。

背景：母库 `kline_daily` 覆盖更全更新，但复权口径不同（后复权/不复权）；
本地 `data.db.daily_bars` 是 akshare 前复权。执行侧（引擎/AI 信号/计价）
默认 **母库优先、缺该标的回退本地**（`data.daily_source=auto`），
可用 `local` 强制只用本地、`mother` 强制只用母库。

本模块只做“读哪份”，不改变复权口径；口径差异由 `integration.mother.compare_bars`
量化并记入文档/Issue。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from paper_trading.models import Bar


def _to_bar(symbol: str, row: dict) -> Bar:
    ts = str(row.get("date") or row.get("timestamp") or "")[:10]
    try:
        when = datetime.fromisoformat(ts) if ts else datetime.now()
    except ValueError:
        when = datetime.now()
    return Bar(symbol=symbol, timestamp=when,
               open=float(row.get("open") or 0.0),
               high=float(row.get("high") or 0.0),
               low=float(row.get("low") or 0.0),
               close=float(row.get("close") or 0.0),
               volume=int(row.get("volume") or 0))


class BarSource:
    """按标的选择行情来源（母库优先/本地兜底）。"""

    def __init__(self, data_db, source: str = "auto",
                 mother: Optional[Path] = None) -> None:
        self.db = data_db
        self.source = (source or "auto").lower()
        self.mother = mother

    def get_bars(self, symbol: str, limit: int = 30) -> list:
        """最近 limit 根日线（时间升序，与本地 get_bars 同序）。"""
        if self.source == "local":
            return self.db.get_bars(symbol, limit=limit)
        try:
            from paper_trading.integration import mother as _m

            blk = _m.daily_bars(
                [symbol], days=limit,
                local_bars=lambda c, d: self.db.get_bars(c, limit=d),
                mother=self.mother)
            b = blk.get(symbol) or {}
            if b.get("available") and b.get("bars"):
                bars = [_to_bar(symbol, x) for x in b["bars"]]
                bars.sort(key=lambda x: x.timestamp)   # 母库返回倒序，统一升序
                return bars[-int(limit):]
        except Exception:                           # noqa: BLE001 - 回退本地
            pass
        return self.db.get_bars(symbol, limit=limit)

    def source_of(self, symbol: str) -> str:
        """该标的当前实际用的来源（用于审计/日志）。"""
        if self.source == "local":
            return "local:akshare"
        try:
            from paper_trading.integration import mother as _m

            blk = _m.daily_bars(
                [symbol], days=30,
                local_bars=lambda c, d: self.db.get_bars(c, limit=d),
                mother=self.mother)
            b = blk.get(symbol) or {}
            return b.get("source") or "local:akshare"
        except Exception:                           # noqa: BLE001
            return "local:akshare"
