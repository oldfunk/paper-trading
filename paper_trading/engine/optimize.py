"""参数网格搜索：在历史数据上批量回测规则变体，用**超额收益**排序。

目的不是"找出历史上最好看的参数"，而是提供一个可复现的筛选器：
同一段数据、同一套费率、同一基准，参数变体之间可比。
样本外验证（walk-forward）与过拟合防护留给上层（见 docs 建议）。

诚实边界：分钟线只覆盖最近几个交易日，所以日内网格（vwap_dev / minute_ma）
的样本天然很小，排序结果只能当"待验证线索"，不能当结论。
"""
from __future__ import annotations

import itertools
from typing import Iterable, Optional

from paper_trading.engine.backtest import (Backtester, FeeModel,
                                            buy_and_hold, metrics)
from paper_trading.engine.rules import RULE_TYPES

#: 依赖分钟线的网格（需要 minutes_by_day 才有意义）
INTRADAY_GRIDS = ("vwap_dev", "minute_ma")

# 预置网格：参数组合是"有先验"的常规区间，不是无脑笛卡尔积
GRIDS: dict[str, dict] = {
    "ma_cross": {
        "base_type": "ma_cross",
        "grid": {"short": [5, 10, 20], "long": [20, 30, 60]},
        "filter": lambda p: int(p["short"]) < int(p["long"]),
    },
    "rsi_zone": {
        "base_type": "rsi_zone",
        "grid": {"n": [6, 14], "lower": [25, 30], "upper": [70, 75]},
        "filter": lambda p: float(p["lower"]) < float(p["upper"]),
    },
    "trailing_stop": {
        "base_type": "trailing_stop",
        "grid": {"pct": [3, 5, 8, 12]},
        "filter": lambda p: True,
    },
    # 日内网格（需分钟线）
    "vwap_dev": {
        "base_type": "vwap_dev",
        "grid": {"pct": [0.5, 1, 2, 3], "min_bars": [5, 15]},
        "filter": lambda p: True,
    },
    "minute_ma": {
        "base_type": "minute_ma_cross",
        "grid": {"short": [3, 5, 8], "long": [15, 30, 60]},
        "filter": lambda p: int(p["short"]) < int(p["long"]),
    },
}

#: 网格名 → 规则类型（minute_ma 是别名，落到 minute_ma_cross）
_GRID_TYPE = {name: spec["base_type"] for name, spec in GRIDS.items()}


def _variants(grid_name: str, symbols: list[str], value: float,
              sell_side: bool = True) -> list[dict]:
    """按网格生成规则变体（交叉类金叉买 + 死叉卖 成对，提高可用性）。"""
    spec = GRIDS.get(grid_name)
    if not spec or not symbols:
        return []
    rtype = spec["base_type"]
    paired = grid_name in ("ma_cross", "rsi_zone")
    keys = list(spec["grid"].keys())
    out: list[dict] = []
    for combo in itertools.product(*(spec["grid"][k] for k in keys)):
        params = dict(zip(keys, combo))
        if not spec["filter"](params):
            continue
        tag = "-".join(f"{params[k]}" for k in keys)
        for sym in symbols:
            out.append({"id": f"g:{grid_name}:{tag}:buy:{sym}", "type": rtype,
                        "symbol": sym, "params": dict(params),
                        "action": {"side": "buy", "value": value}, "once": False})
            if sell_side and paired:
                out.append({"id": f"g:{grid_name}:{tag}:sell:{sym}", "type": rtype,
                            "symbol": sym, "params": dict(params),
                            "action": {"side": "sell", "all": True}, "once": False})
    return out


def grid_search(cfg: dict, bars_by_sym: dict[str, list], days: list[str],
                grid_name: str, symbols: Optional[list[str]] = None,
                value: float = 10000.0, fee: Optional[FeeModel] = None,
                initial_cash: float = 100000.0,
                slippage_pct: float = 0.0,
                top: int = 10,
                minutes_by_day: Optional[dict] = None,
                min_trades: int = 1) -> dict:
    """对单个网格做参数搜索；返回按超额收益排序的变体结果。

    min_trades：至少成交多少笔才算"一条策略"。**默认剔除 0 笔的变体**——
    熊市里"什么都不做"会以 0% 收益跑赢任何真亏钱的策略，把这种空结果排第一
    等于给出"最佳参数是不交易"这种荒谬结论。它们单独统计在 dropped 里。
    """
    fee = fee or FeeModel()
    symbols = list(symbols or [])
    variants = _variants(grid_name, symbols, value)
    if not variants:
        return {"error": f"网格 {grid_name} 无有效变体或未指定标的",
                "grids": list(GRIDS)}
    minutes_by_day = minutes_by_day or {}
    # min_trades=0 显式表示"不过滤"；None/缺省为 1（剔除 0 笔的空结果）
    _min_trades = 1 if min_trades is None else max(0, int(min_trades))
    intraday = grid_name in INTRADAY_GRIDS
    if intraday and not minutes_by_day:
        return {"error": f"网格 {grid_name} 依赖分钟线，本地没有分钟数据"
                          f"（先 minute pull）", "grids": list(GRIDS)}
    bench = buy_and_hold(bars_by_sym, days, initial_cash, fee)
    bench_ret = bench.get("total_return_pct", 0.0)

    # 按参数分组评估（金叉+死叉是一组，不能拆开单算）
    groups: dict[str, list[dict]] = {}
    for r in variants:
        tag = r["id"].split(":")[2] + ":" + r["symbol"]
        groups.setdefault(tag, []).append(r)

    results = []
    dropped: list[dict] = []
    for tag, rules in groups.items():
        trial = dict(cfg)
        trial["rules"] = rules
        trial.setdefault("min_ticket_cny", 1000)
        trial.setdefault("max_orders_per_day", 10 ** 6)
        trial["max_turnover_per_day"] = 0
        res = Backtester(trial, fee=fee, slippage_pct=slippage_pct).run(
            bars_by_sym, days, initial_cash=initial_cash,
            minutes_by_day=minutes_by_day if intraday else None)
        m = metrics(res, len(days))
        pf = m.get("profit_factor")
        rec = {
            "tag": tag, "symbol": rules[0]["symbol"], "params": rules[0]["params"],
            "ret_pct": m["total_return_pct"], "mdd_pct": m["max_drawdown_pct"],
            "sharpe": m["sharpe_annual"], "trades": m["trades"],
            "win_pct": m["win_rate_pct"],
            "profit_factor": pf if isinstance(pf, (int, float)) else None,
            "fees": m["total_fees"],
            "excess_pct": round(m["total_return_pct"] - bench_ret, 2),
        }
        if int(m.get("trades") or 0) < _min_trades:
            dropped.append(rec)
            continue
        results.append(rec)
    results.sort(key=lambda x: (x["excess_pct"], -x["mdd_pct"]), reverse=True)
    dropped.sort(key=lambda x: -x["excess_pct"])
    best = results[0] if results else None
    note = "超额=变体收益−等权买入持有；样本内结果，需样本外复验"
    if intraday:
        note += "；分钟线只覆盖最近数日，样本极小，结论仅供参考"
    if dropped:
        note += (f"；另有 {len(dropped)} 组参数在该样本内几乎不触发（成交不足 "
                 f"{_min_trades} 笔），已剔除不参与排名——否则熊市里「什么都不做」"
                 f"会排第一")
    return {"grid": grid_name, "rule_type": _GRID_TYPE.get(grid_name),
            "symbols": symbols, "days": len(days),
            "intraday": intraday,
            "intraday_days": res.intraday_days if intraday else 0,
            "benchmark_pct": bench_ret, "variants": len(results),
            "dropped_no_trade": len(dropped),
            "dropped_top": dropped[:3],
            "best": best, "top": results[:top],
            "note": note}


def available_grids() -> Iterable[str]:
    return tuple(GRIDS)