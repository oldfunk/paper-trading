"""规则引擎回测：用已存日线重放规则，产出可量化的收益曲线。

纪律（与实盘共用同一套 rules.py 判定，杜绝"回测好看实盘亏"）：
- T 日收盘算出的信号，最早 T+1 开盘执行（无未来函数）
- 费用按 config 同一套费率与最低佣金（含印花税/过户费）
- 买入按金额换整手；低于 min_ticket 不下单；日内笔数/金额上限同样生效
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from paper_trading.engine.rules import (INTRADAY_TYPES, Intent, PositionView,
                                         RuleContext, evaluate, size_for_value)


@dataclass
class FeeModel:
    """与 PaperBroker 同一套费率（parity 由单测锁定）。"""
    commission_rate: float = 0.00025
    commission_min: float = 5.0
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001

    def buy_cost(self, amount: float) -> tuple[float, float]:
        commission = max(amount * self.commission_rate, self.commission_min)
        return commission, amount * self.transfer_fee_rate

    def sell_cost(self, amount: float) -> tuple[float, float, float]:
        commission = max(amount * self.commission_rate, self.commission_min)
        return (commission, amount * self.stamp_duty_rate,
                amount * self.transfer_fee_rate)


@dataclass
class BTPosition:
    volume: int = 0
    avg_cost: float = 0.0
    entry_day: str = ""
    peak: float = 0.0


@dataclass
class BacktestResult:
    start: str = ""
    end: str = ""
    initial_cash: float = 0.0
    final_equity: float = 0.0
    equity_curve: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    total_fees: float = 0.0
    skipped: list[dict] = field(default_factory=list)
    per_rule: dict[str, dict] = field(default_factory=dict)
    intraday_days: int = 0          # 有分钟线并重放了日内规则的天数（0=未重放）


class Backtester:
    """日线重放回测器（无 LLM、无网络、纯本地数据）。"""

    def __init__(self, cfg: dict, fee: Optional[FeeModel] = None,
                 slippage_pct: float = 0.0):
        self.cfg = cfg
        self.fee = fee or FeeModel()
        self.slippage = float(slippage_pct or 0.0)
        self.min_ticket = float(cfg.get("min_ticket_cny") or 0.0)
        self.max_orders = int(cfg.get("max_orders_per_day") or 0) or 10 ** 9
        self.max_turnover = float(cfg.get("max_turnover_per_day") or 0.0) or float("inf")

    # ---------- 内部工具 ----------

    def _exec_price(self, ref: float, side: str) -> float:
        px = ref * (1.0 + self.slippage if side == "buy" else 1.0 - self.slippage)
        return round(px, 2)

    def _buy(self, cash: float, sym: str, value: float, price: float,
             day: str) -> tuple[float, Optional[dict], Optional[dict]]:
        """返回 (新现金, 成交记录, 跳过原因)。"""
        vol = size_for_value(price, value)
        if vol <= 0:
            return cash, None, {"day": day, "symbol": sym, "why": "金额不足一手"}
        amount = price * vol
        if value < self.min_ticket:
            return cash, None, {"day": day, "symbol": sym, "why": "低于最小下单金额",
                                "value": value}
        commission, transfer = self.fee.buy_cost(amount)
        if amount + commission + transfer > cash:
            return cash, None, {"day": day, "symbol": sym, "why": "现金不足",
                                "need": round(amount + commission + transfer, 2),
                                "cash": round(cash, 2)}
        trade = {"day": day, "symbol": sym, "side": "buy", "volume": vol,
                 "price": price, "amount": round(amount, 2),
                 "commission": round(commission, 4),
                 "transfer_fee": round(transfer, 4), "stamp_duty": 0.0}
        return cash - amount - commission - transfer, trade, None

    def _sell(self, pos: BTPosition, sym: str, volume: int, price: float,
              day: str, reason: str) -> tuple[BTPosition, Optional[dict]]:
        volume = min(volume, pos.volume)
        if volume <= 0:
            return pos, None
        amount = price * volume
        commission, stamp, transfer = self.fee.sell_cost(amount)
        proceeds = amount - commission - stamp - transfer
        pnl = proceeds - pos.avg_cost * volume
        remaining = pos.volume - volume
        pos.volume = remaining
        return pos, {"day": day, "symbol": sym, "side": "sell", "volume": volume,
                     "price": price, "amount": round(amount, 2),
                     "commission": round(commission, 4),
                     "stamp_duty": round(stamp, 4),
                     "transfer_fee": round(transfer, 4),
                     "pnl": round(pnl, 2), "reason": reason}

    # ---------- 主循环 ----------

    def run(self, bars_by_sym: dict[str, list], days: list[str],
            initial_cash: float = 100000.0,
            live: Optional[dict] = None,
            minutes_by_day: Optional[dict] = None) -> BacktestResult:
        """bars_by_sym: {sym: [Bar...]按时间升序}；days: 交易日列表（升序）。

        minutes_by_day: {(sym, day): [(ts,o,h,l,c,v,a)...按序]}，
        供日内规则（vwap_dev / intraday_pullback / minute_ma_cross）重放。
        没有分钟数据时日内规则不触发，并在 skipped 里说明原因（不静默"跑过了"）。
        """
        res = BacktestResult(initial_cash=initial_cash)
        all_rules = [r for r in (self.cfg.get("rules") or []) if isinstance(r, dict)]
        rules = [r for r in all_rules if str(r.get("type") or "") not in INTRADAY_TYPES]
        intra_rules = [r for r in all_rules if str(r.get("type") or "") in INTRADAY_TYPES]
        minutes_by_day = minutes_by_day or {}
        if intra_rules and not minutes_by_day:
            res.skipped.append({"why": "规则集含日内规则，但无分钟线数据，"
                                       "日内规则全程未触发（先 minute pull）"})
        fired: set[str] = set()
        pending: list[tuple[str, Intent, str]] = []      # (rule_id, intent, exec_day)
        pos: dict[str, BTPosition] = {}
        cash = float(initial_cash)
        equity_curve: list[dict] = []
        fees_total = 0.0
        day_orders = 0
        day_turnover = 0.0
        intraday_days: set[str] = set()
        last_px: dict[str, float] = {}

        for day in days:
            # px：当日真实收盘（没有就不成交）；px_last：沿用最近一次有效收盘（算市值用）
            # 分开的原因：某只票当天没数据时，市值不能当 0（否则权益曲线凭空塌一块），
            # 但也不能拿旧价假装成交。
            px = {s: self._day_px(b, day) for s, b in bars_by_sym.items()}
            for s, v in px.items():
                if v:
                    last_px[s] = v
            px_last = {s: last_px.get(s) for s in bars_by_sym}
            held_days = {s: max(0, days.index(day) - days.index(p.entry_day))
                         for s, p in pos.items() if p.entry_day in days}
            # 当日买入的部分 T+1 冻结：日内规则与实盘一样卖不掉
            frozen: dict[str, int] = {}

            # 1) 执行昨日挂单（开盘价：prev close 近似 = 昨日收盘；无则以当日收盘）
            for rid, it, exec_day in pending:
                if exec_day != day:
                    continue
                if rid in fired:
                    continue
                ref = px.get(it.symbol)
                if not ref:
                    continue
                price = self._exec_price(ref, it.side)
                if it.side == "buy":
                    if day_orders >= self.max_orders or day_turnover >= self.max_turnover:
                        res.skipped.append({"day": day, "symbol": it.symbol,
                                            "why": "日内上限"})
                        continue
                    act = (next((r for r in rules if r["id"] == rid), {})
                           .get("action") or {})
                    value = float(act.get("value") or 0) or 10000.0
                    cash, tr, skip = self._buy(cash, it.symbol, value, price, day)
                    if skip or not tr:
                        res.skipped.append({"day": day, "symbol": it.symbol, **(skip or {})})
                        continue
                    p = pos.setdefault(it.symbol, BTPosition())
                    amount = tr["amount"] + tr["commission"] + tr["transfer_fee"]
                    p.avg_cost = ((p.avg_cost * p.volume + amount) /
                                  (p.volume + tr["volume"])
                                  if p.volume else amount / tr["volume"])
                    p.volume += tr["volume"]
                    p.entry_day = p.entry_day or day
                    p.peak = max(p.peak, price)
                    frozen[it.symbol] = frozen.get(it.symbol, 0) + tr["volume"]
                else:
                    p = pos.get(it.symbol)
                    if not p or p.volume <= 0:
                        res.skipped.append({"day": day, "symbol": it.symbol,
                                            "why": "无可卖持仓"})
                        continue
                    act = (next((r for r in rules if r["id"] == rid), {})
                           .get("action") or {})
                    vol = p.volume if act.get("all") else (
                        int(act.get("volume") or 0) or p.volume)
                    pos[it.symbol], tr = self._sell(p, it.symbol, vol, price, day,
                                                     it.reason)
                    if not tr:
                        continue
                if tr:
                    fees = tr["commission"] + tr["stamp_duty"] + tr["transfer_fee"]
                    fees_total += fees
                    tr["rule"] = rid
                    res.trades.append(tr)
                    day_orders += 1
                    day_turnover += tr["amount"]
                    st = res.per_rule.setdefault(rid, {"trades": 0, "pnl": 0.0,
                                                        "fees": 0.0})
                    st["trades"] += 1
                    st["fees"] += fees
                    if "pnl" in tr:
                        st["pnl"] += tr["pnl"]
                    rule_cfg = next((r for r in rules if r["id"] == rid), {})
                    if rule_cfg.get("once", True):
                        fired.add(rid)
            pending = [p for p in pending if p[2] != day]

            # 1.5) 日内规则逐分钟重放（只用已走完的分钟，无未来函数）
            if intra_rules:
                cash, day_orders, day_turnover, replayed = self._replay_intraday(
                    intra_rules, day, minutes_by_day, px_last, pos, cash,
                    day_orders, day_turnover, frozen, held_days, res)
                if replayed:
                    intraday_days.add(day)

            # 2) 收盘盯市 + 峰值更新（缺失日沿用最近收盘，不当 0）
            mv = 0.0
            for s, p in pos.items():
                c = px_last.get(s)
                if c:
                    mv += c * p.volume
                    p.peak = max(p.peak, c)
            equity = cash + mv
            equity_curve.append({"day": day, "cash": round(cash, 2),
                                 "market_value": round(mv, 2),
                                 "equity": round(equity, 2)})

            # 3) 用今日收盘判定规则 → 明日开盘挂单
            if day != days[-1]:
                next_day = days[days.index(day) + 1]
                for r in rules:
                    rid = r["id"]
                    if rid in fired and bool(r.get("once", True)):
                        continue
                    syms = r.get("symbols") or [r.get("symbol")]
                    for sym in syms:
                        b = bars_by_sym.get(sym) or []
                        closes = [x.close for x in b
                                  if x.timestamp.date().isoformat() <= day]
                        if len(closes) < 2:
                            continue
                        ctx = RuleContext(now=f"{day}T15:00:00", day=day, symbol=sym,
                                          price=closes[-1], prev_close=closes[-2],
                                          day_high=max(x.high for x in b[-1:]),
                                          day_low=min(x.low for x in b[-1:]),
                                          closes=closes,
                                          position=PositionView(
                                              symbol=sym,
                                              total_volume=pos.get(sym, BTPosition()).volume,
                                              avg_cost=pos.get(sym, BTPosition()).avg_cost,
                                              entry_day=pos.get(sym, BTPosition()).entry_day,
                                              peak_price=pos.get(sym, BTPosition()).peak),
                                          trading_days_held=held_days.get(sym, 0))
                        it = evaluate(r, ctx)
                        if it:
                            pending.append((rid, it, next_day))
                            break

        res.start = days[0] if days else ""
        res.end = days[-1] if days else ""
        res.equity_curve = equity_curve
        res.final_equity = equity_curve[-1]["equity"] if equity_curve else initial_cash
        res.total_fees = round(fees_total, 2)
        res.intraday_days = len(intraday_days)
        return res

    # ---------- 日内规则重放 ----------

    def _replay_intraday(self, rules: list[dict], day: str, minutes_by_day: dict,
                         px: dict, pos: dict, cash: float, day_orders: int,
                         day_turnover: float, frozen: dict, held_days: dict,
                         res: BacktestResult) -> tuple[float, int, float, bool]:
        """逐分钟重放日内规则。

        成交价 = 触发那一分钟的收盘价（引擎每 60 秒一跳，与实盘同口径：
        只用已走完的那一分钟，不偷看下一分钟）。每规则每天最多触发一次。
        """
        syms = sorted({str(k[0]) for k in minutes_by_day
                       if isinstance(k, tuple) and len(k) == 2 and k[1] == day})
        replayed = False
        for sym in syms:
            mins = minutes_by_day.get((sym, day)) or []
            if len(mins) < 2:
                continue
            replayed = True
            done: set[str] = set()
            for i in range(1, len(mins)):
                window = mins[:i + 1]                  # 只到当前分钟为止
                m = window[-1]
                price = float(m[4] or 0)
                if price <= 0:
                    continue
                for r in rules:
                    rid = str(r.get("id") or "")
                    if rid in done:
                        continue
                    rule_syms = [str(s) for s in (r.get("symbols")
                                                  or [r.get("symbol")])]
                    if sym not in rule_syms:
                        continue
                    p = pos.get(sym) or BTPosition()
                    avail = max(0, p.volume - int(frozen.get(sym, 0)))
                    ctx = RuleContext(now=str(m[0]), day=day, symbol=sym, price=price,
                                      prev_close=float(px.get(sym) or price),
                                      day_high=max(float(x[2] or 0) for x in window),
                                      day_low=min(float(x[3] or 0) for x in window),
                                      change_pct=0.0, closes=[],
                                      position=PositionView(
                                          symbol=sym, total_volume=p.volume,
                                          available_volume=avail,
                                          avg_cost=p.avg_cost, entry_day=p.entry_day,
                                          peak_price=p.peak),
                                      trading_days_held=held_days.get(sym, 0),
                                      minutes=window)
                    it = evaluate(r, ctx)
                    if not it:
                        continue
                    done.add(rid)
                    exec_px = self._exec_price(price, it.side)
                    act = r.get("action") or {}
                    if it.side == "buy":
                        if (day_orders >= self.max_orders
                                or day_turnover >= self.max_turnover):
                            res.skipped.append({"day": day, "symbol": sym,
                                                "why": "日内上限"})
                            continue
                        value = float(act.get("value") or 0) or 10000.0
                        cash, tr, skip = self._buy(cash, sym, value, exec_px, day)
                        if skip or not tr:
                            res.skipped.append({"day": day, "symbol": sym, **(skip or {})})
                            continue
                        p = pos.setdefault(sym, BTPosition())
                        amount = tr["amount"] + tr["commission"] + tr["transfer_fee"]
                        p.avg_cost = ((p.avg_cost * p.volume + amount) /
                                      (p.volume + tr["volume"])
                                      if p.volume else amount / tr["volume"])
                        p.volume += tr["volume"]
                        p.entry_day = p.entry_day or day
                        p.peak = max(p.peak, exec_px)
                        frozen[sym] = frozen.get(sym, 0) + tr["volume"]
                    else:
                        p = pos.get(sym)
                        avail = max(0, (p.volume if p else 0) - int(frozen.get(sym, 0)))
                        if not p or avail <= 0:
                            res.skipped.append({"day": day, "symbol": sym,
                                                "why": "无可卖持仓（T+1 或空仓）"})
                            continue
                        vol = avail if (it.all_out or act.get("all")) else min(
                            int(act.get("volume") or 0) or avail, avail)
                        vol = (vol // 100) * 100
                        if vol <= 0:
                            continue
                        pos[sym], tr = self._sell(p, sym, vol, exec_px, day, it.reason)
                        if not tr:
                            continue
                    tr["rule"] = rid
                    tr["ts"] = str(m[0])
                    res.trades.append(tr)
                    fees = tr["commission"] + tr["stamp_duty"] + tr["transfer_fee"]
                    res.total_fees = round(res.total_fees + fees, 2)
                    day_orders += 1
                    day_turnover += tr["amount"]
                    st = res.per_rule.setdefault(rid, {"trades": 0, "pnl": 0.0,
                                                        "fees": 0.0})
                    st["trades"] += 1
                    st["fees"] = round(st["fees"] + fees, 2)
                    if "pnl" in tr:
                        st["pnl"] = round(st["pnl"] + tr["pnl"], 2)
        return cash, day_orders, day_turnover, replayed

    @staticmethod
    def _day_px(bars: list, day: str) -> Optional[float]:
        for x in reversed(bars):
            if x.timestamp.date().isoformat() == day:
                return x.close
        return None


# ---------- 指标 ----------

def load_minutes(data_db, syms: list[str], days: list[str]) -> dict:
    """从本地库读分钟线，整理成 {(sym, day): [(ts,o,h,l,c,v,a)...] 按序}。

    只取 days 覆盖的交易日；库里没有就返回空 dict（回测会明确提示
    "日内规则未触发"），绝不假装跑过。
    """
    want = set(days or [])
    out: dict = {}
    for sym in syms or []:
        try:
            rows = data_db.get_minute_bars(sym, limit=4000)
        except Exception:       # noqa: BLE001 - 表不存在/读失败都退化为无分钟数据
            continue
        for r in rows:
            ts = str(r.get("ts") or "")
            day = ts[:10]
            if day not in want or len(ts) < 16:
                continue
            out.setdefault((sym, day), []).append(
                (ts, float(r.get("open") or 0), float(r.get("high") or 0),
                 float(r.get("low") or 0), float(r.get("close") or 0),
                 float(r.get("volume") or 0), float(r.get("amount") or 0)))
    for k in out:
        out[k].sort(key=lambda x: x[0])
    return out


def buy_and_hold(bars_by_sym: dict[str, list], days: list[str],
                 initial_cash: float = 100000.0,
                 fee: Optional[FeeModel] = None) -> dict:
    """基准：等权买入持有（含一次性买入费用），用于对照策略曲线。"""
    fee = fee or FeeModel()
    syms = [s for s in bars_by_sym if bars_by_sym[s]]
    if not syms:
        return {}
    px = {s: Backtester._day_px(bars_by_sym[s], days[0]) for s in syms}
    px = {s: v for s, v in px.items() if v}
    if not px:
        return {}
    per = initial_cash / len(px)
    fees = 0.0
    holdings: dict[str, int] = {}
    spent = 0.0
    for s, p in px.items():
        vol = int(per / p / 100) * 100
        if vol <= 0:
            continue
        amount = p * vol
        c, t = fee.buy_cost(amount)
        while vol > 0 and amount + c + t > per:   # 费用挤破预算就减一手
            vol -= 100
            if vol <= 0:
                break
            amount = p * vol
            c, t = fee.buy_cost(amount)
        if vol <= 0:
            continue
        holdings[s] = vol
        fees += c + t
        spent += amount + c + t
    leftover = initial_cash - spent          # 未买满整手的余额留在现金
    curve = []
    last_px: dict[str, float] = {}
    for d in days:
        mv = 0.0
        for s, vol in holdings.items():
            c = Backtester._day_px(bars_by_sym[s], d)
            if c:                 # 当日有收盘就用它；没有就沿用最近一次有效收盘
                last_px[s] = c
            c = last_px.get(s)
            if c:
                mv += c * vol
        curve.append({"day": d, "equity": round(leftover + mv, 2)})
    res = BacktestResult(initial_cash=initial_cash, equity_curve=curve,
                         total_fees=round(fees, 2), start=days[0], end=days[-1])
    res.final_equity = curve[-1]["equity"] if curve else initial_cash
    return {"symbols": sorted(holdings), "final_equity": round(res.final_equity, 2),
            "total_return_pct": round((res.final_equity / initial_cash - 1) * 100, 2),
            "total_fees": res.total_fees}


def metrics(res: BacktestResult, days: int) -> dict:
    curve = res.equity_curve
    if not curve:
        return {"error": "无数据"}
    eq = [c["equity"] for c in curve]
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    wins = [t for t in res.trades if t.get("side") == "sell" and t.get("pnl", 0) > 0]
    losses = [t for t in res.trades if t.get("side") == "sell" and t.get("pnl", 0) <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    total_ret = (res.final_equity / res.initial_cash - 1.0) if res.initial_cash else 0.0
    ann = ((1.0 + total_ret) ** (244.0 / max(days, 1)) - 1.0) if total_ret > -1 else -1.0
    rets = []
    for a, b in zip(eq, eq[1:]):
        if a > 0:
            rets.append(b / a - 1.0)
    mu = sum(rets) / len(rets) if rets else 0.0
    var = (sum((r - mu) ** 2 for r in rets) / len(rets)) if len(rets) > 1 else 0.0
    sharpe = (mu / (var ** 0.5) * (244 ** 0.5)) if var > 0 else 0.0
    return {
        "start": res.start, "end": res.end, "days": days,
        "initial_cash": round(res.initial_cash, 2),
        "final_equity": round(res.final_equity, 2),
        "total_return_pct": round(total_ret * 100, 2),
        "annualized_pct": round(ann * 100, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "sharpe_annual": round(sharpe, 2),
        "trades": len(res.trades),
        "closed_trades": len(wins) + len(losses),
        "win_rate_pct": round(len(wins) / max(len(wins) + len(losses), 1) * 100, 1),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else
                         (float("inf") if gross_win > 0 else 0.0),
        "total_fees": res.total_fees,
        "fee_drag_pct": round(res.total_fees / res.initial_cash * 100, 2)
        if res.initial_cash else 0.0,
        "skipped": len(res.skipped),
    }