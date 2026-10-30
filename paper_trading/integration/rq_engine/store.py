"""原始价 DayBarStore：把 `daily_bars_raw` 喂成 rqalpha 的 bar 结构化数组。

契约（实测 rqalpha 6.4.2 `data/base_data_source/storages.py:226-238` +
`data_source.py:230-243`）：`get_bars(order_book_id)` 返回按 `datetime` 升序的
numpy 结构化数组，字段至少 `datetime/open/close/high/low/volume/total_turnover`，
日线撮合与涨跌停还要 `limit_up/limit_down`（bundle 的 `SecuritiesDayBarStore`
就多这两列）。rqalpha 在**读的时候**按 `ex_cum_factor` 复权（`adjust.py:30-77`），
所以 store 里必须是**原始价**——这正是 T31 管道的产出。

鸭子类型：本模块不 import rqalpha（产品 venv 不装它），
`BaseDataSource.register_day_bar_store` 只要求 `get_bars/get_date_range` 存在。
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Iterable, Optional

import numpy as np

from paper_trading.utils import get_logger
from .bands import limit_prices

logger = get_logger(__name__)

BAR_DTYPE = np.dtype([
    ("datetime", np.int64),
    ("open", np.float64),
    ("close", np.float64),
    ("high", np.float64),
    ("low", np.float64),
    ("volume", np.float64),
    ("total_turnover", np.float64),
    ("prev_close", np.float64),
    ("limit_up", np.float64),
    ("limit_down", np.float64),
])

#: G-T13-6 的兄弟检查：qfq 的 OHLC 拼 raw 的 close 会出现这种不自洽
OHLC_ORDER = ("high", "low")


class BandMissingError(RuntimeError):
    """G-T13-3：band/prev_close 供不齐 ⇒ 拒供该日（rqalpha 缺字段会 fail-open）。"""


def to_order_book_id(symbol: str) -> str:
    code = str(symbol or "").strip().zfill(6)
    if code[0] in ("6", "9"):
        return f"{code}.XSHG"
    if code[0] in ("4", "8"):
        return f"{code}.BJ"
    return f"{code}.XSHE"


def to_symbol(order_book_id: str) -> str:
    return str(order_book_id).split(".")[0]


def _d2i(d: date) -> int:
    """rqalpha 的 bar datetime 编码 YYYYMMDD000000（`datetime_func.py:35-41`）。"""
    return d.year * 10_000_000_000 + d.month * 100_000_000 + d.day * 1_000_000


def _as_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v)[:10]).date()


class RawDayBarStore:
    """`AbstractDayBarStore` 的鸭子实现。

    `require_bands=True`（默认）：只要**最新一天**或**任何中间日**缺
    `prev_close/limit_up/limit_down` 就抛 `BandMissingError`——不许带病供数。
    序列首根例外（没有前收盘可用），它一旦被当成"今日"也会被最新日检查拦住。
    """

    def __init__(self, bars_by_symbol: dict[str, list], *, today: Optional[date] = None,
                 require_bands: bool = True, is_st: Optional[Iterable[str]] = None,
                 st_set: Optional[set] = None) -> None:
        del is_st  # 新规下 ST 与同板块同幅，不需要标记；参数保留逼调用方表态
        self.today = _as_date(today) if today else None
        self.require_bands = require_bands
        self.st_set = set(st_set or ())
        self._bars: dict[str, np.ndarray] = {}
        self.stats: dict[str, dict] = {}
        self.gate_rejected: dict[str, str] = {}      # 被参照闸挡下的标的 → 原因
        self._build(bars_by_symbol)

    @classmethod
    def from_data_db(cls, db, symbols, *, days: int = 260, today=None,
                     require_bands: bool = True, st_set: Optional[set] = None,
                     require_today_reference: bool = True):
        """从 `DataDBManager` 的 raw 表读（引擎唯一允许的数据源）。

        `require_today_reference`（T50-C）：**今天这根没有独立参照的标的直接不供**，
        而不是降级喂进去——母库 T 日 `close_raw` 为 NULL 且盘后快照也没对上的日子，
        引擎就该"缺数据不下单"。被挡的标的记在 `gate_rejected` 里，随 evidence 出，
        这样"为什么今天没成交"是可审计的，不是一句"无 bar"。
        """
        from paper_trading.data.raw_feed import today_reference_state

        data = {}
        rejected = {}
        today_d = _as_date(today) if today is not None else None
        for s in symbols:
            sym = to_symbol(s) if "." in str(s) else str(s)
            if require_today_reference and today_d is not None:
                st = today_reference_state(db, sym, today=today_d)
                if st["state"] == "unchecked":
                    rejected[sym] = "today_bar_unchecked"
                    continue
            rows = db.get_raw_bars(sym, limit=days)
            if rows:
                data[str(s)] = rows
        store = cls(data, today=today, require_bands=require_bands, st_set=st_set)
        store.gate_rejected = rejected
        if rejected:
            logger.warning("G-T13-1-C 当日无独立参照，拒供：%s", rejected)
        return store

    # ---- 构建 ----------------------------------------------------------
    def _build(self, bars_by_symbol: dict[str, list]) -> None:
        problems: list[str] = []
        for symbol, rows in bars_by_symbol.items():
            obid = to_order_book_id(symbol)
            series = sorted((_as_date(r.timestamp if hasattr(r, "timestamp") else r["timestamp"]),
                             r) for r in rows)
            if self.today is not None:
                series = [(d, r) for d, r in series if d <= self.today]
            if not series:
                continue
            arr = np.zeros(len(series), dtype=BAR_DTYPE)
            # 这三列 + 成交额必须显式 NaN 起步：np.zeros 会留下 0.0，
            # 而 rqalpha 的 is_valid_price 把 0 当无效 ⇒ 涨跌停闸静默失效
            for col in ("prev_close", "limit_up", "limit_down", "total_turnover"):
                arr[col] = math.nan
            for i, (d, r) in enumerate(series):
                o, h, l, c = (float(r.open), float(r.high), float(r.low), float(r.close))
                if not all(x > 0 and math.isfinite(x) for x in (o, h, l, c)):
                    problems.append(f"{symbol} {d}：OHLC 存在非法值")
                    continue
                if not (h >= max(o, c) and l <= min(o, c) and h >= l):
                    problems.append(f"{symbol} {d}：OHLC 不自洽（混合口径特征）")
                    continue
                arr[i]["datetime"] = _d2i(d)
                arr[i]["open"], arr[i]["close"] = o, c
                arr[i]["high"], arr[i]["low"] = h, l
                arr[i]["volume"] = float(r.volume or 0)
                amt = getattr(r, "amount", None)
                arr[i]["total_turnover"] = float(amt) if amt else math.nan
                if i > 0:
                    pc = arr[i - 1]["close"]
                    up, down = limit_prices(pc, symbol, is_st=(symbol in self.st_set))
                    arr[i]["prev_close"] = pc
                    arr[i]["limit_up"], arr[i]["limit_down"] = up, down
            arr = arr[arr["datetime"] > 0]
            self._bars[obid] = arr
            self.stats[obid] = {
                "symbol": symbol, "bars": int(len(arr)),
                "first": str(_date_from_int(arr["datetime"][0])) if len(arr) else None,
                "last": str(_date_from_int(arr["datetime"][-1])) if len(arr) else None,
                "bands_ok": int(np.isfinite(arr["limit_up"]).sum()) if len(arr) else 0,
                "turnover_ok": int(np.isfinite(arr["total_turnover"]).sum()) if len(arr) else 0,
            }
            if self.require_bands:
                # 最新一天必须有 band（引擎今天就要下单）
                if len(arr) and not math.isfinite(arr["limit_up"][-1]):
                    problems.append(
                        f"{symbol} 最新日 {self.stats[obid]['last']} 无涨跌停带"
                        f"（缺前收盘？）——rqalpha 会 fail-open，拒供")
                # 中间日也不许断带
                miss = int(np.count_nonzero(~np.isfinite(arr["limit_up"][1:]))) if len(arr) > 1 else 0
                if miss:
                    problems.append(f"{symbol} 序列中段缺带 {miss} 根")
        if problems and self.require_bands:
            raise BandMissingError("G-T13-3 band/OHLC 门禁不过：" + "；".join(problems[:8]))
        self.problems = problems

    # ---- AbstractDayBarStore 接口 ------------------------------------
    def get_bars(self, order_book_id: str) -> np.ndarray:
        return self._bars.get(order_book_id, np.empty(0, dtype=BAR_DTYPE))

    def get_date_range(self, order_book_id: str):
        bars = self._bars.get(order_book_id)
        if bars is None or len(bars) == 0:
            return _d2i(self.today or date.today()), _d2i(self.today or date.today())
        return int(bars["datetime"][0]), int(bars["datetime"][-1])

    def order_book_ids(self) -> list:
        return sorted(self._bars)

    def first_date(self) -> Optional[date]:
        """全池最早一根 bar 的日期（`available_data_range` 左端用）。"""
        days = [v["first"] for v in self.stats.values() if v.get("first")]
        return date.fromisoformat(min(days)) if days else self.today


def _date_from_int(v: int) -> date:
    s = str(int(v))
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
