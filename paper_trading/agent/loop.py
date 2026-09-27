"""AI 交易员（P2：日内一次决策，定时主动跑）。

与 HermesBridge.run_daily（MA 规则）互斥：同一账户同一天只跑其一，
由 cron 二选一。本循环自带“今日已决策”幂等闸。
"""
from __future__ import annotations

import json as _json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from paper_trading.hermes_bridge import HermesBridge

from paper_trading.agent.prompts import TRADER_SYSTEM, USER_TMPL


@dataclass
class AgentConfig:
    enabled: bool = True
    max_orders_per_run: int = 3
    max_order_value: float = 20000.0
    daily_loss_halt_pct: float = 0.05  # 累计浮亏超此比例，当天停手


def _extract_json(text: str) -> Optional[dict]:
    """从 LLM 输出中提取第一个 {...} 并解析，失败返回 None（fail-closed）。"""
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        obj = _json.loads(text[start:end])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


class AgentTrader:
    """日内一次的 AI 决策循环。"""

    def __init__(self, bridge: "HermesBridge", cfg: Optional[AgentConfig] = None) -> None:
        self.bridge = bridge
        self.cfg = cfg or AgentConfig()

    def _decided_today(self) -> bool:
        """今日是否已有实质决策（跳过类流水不算，避免早盘空跑锁死午后真跑）。"""
        today = datetime.now().date().isoformat()
        for o in self.bridge.broker.get_op_log(50):
            if o["action"] != "ai:decide" or not o["ok"]:
                continue
            if str(o["timestamp"])[:10] != today:
                continue
            try:
                import json as _json

                res = _json.loads(o["result"] or "{}")
            except Exception:
                res = {}
            if "skipped" not in res:
                return True
        return False

    def run(self, symbols: list[str], dry_run: bool = False,
            force: bool = False) -> dict:
        from paper_trading.models import Order, OrderType
        from paper_trading.utils import get_logger

        logger = get_logger(__name__)
        b = self.bridge
        now = datetime.now().isoformat()
        mode = "dry" if dry_run else "live"

        if not dry_run and not force and self._decided_today():
            logger.warning("AI already decided today, skip (idempotency)")
            b.broker.log_operation("ai:decide", {"symbols": symbols, "mode": mode},
                                   True, {"skipped": "already-decided"}, None, None)
            return {"ok": True, "skipped": "already-decided", "timestamp": now,
                    "symbols": symbols}

        # 1. 同步行情（与 run 同一口径）
        b.sync_data(symbols)

        # 2. 取数 + 无新鲜数据守卫
        all_bars: dict[str, list] = {}
        latest: dict[str, float] = {}
        for sym in symbols:
            bars = b.data_db.get_bars(sym, limit=30)
            if bars:
                all_bars[sym] = bars
                latest[sym] = bars[-1].close
        today = datetime.now().date()
        if not any(bars and bars[-1].timestamp.date() >= today for bars in all_bars.values()):
            logger.warning("No fresh bars, AI skips")
            b.broker.log_operation("ai:decide", {"symbols": symbols, "mode": mode},
                                   True, {"skipped": "no-fresh-bars"}, None, None)
            return {"ok": True, "skipped": "no-fresh-bars", "timestamp": now,
                    "symbols": symbols}

        # 3. T+1 解冻（dry-run 不碰账本）
        if not dry_run:
            b.broker.unfreeze_t1()

        # 4. 风控基线：峰值/回撤熔断 + 日亏熔断
        cash = b.broker.get_cash()
        positions = {p.symbol: p for p in b.broker.get_all_positions()}
        nav = b.broker.get_nav(latest)
        b.risk.update_peak(nav.total_value)
        halted, dd = b.risk.check_drawdown(nav.total_value)
        if halted:
            logger.error(f"Drawdown halt {dd:.2%}, AI skips")
            b.broker.log_operation("ai:decide", {"symbols": symbols, "mode": mode},
                                   False, {"skipped": "drawdown-halt",
                                           "drawdown": round(dd, 4)}, None, None)
            return {"ok": False, "error": f"回撤熔断 {dd:.2%}", "timestamp": now}
        if nav.pnl_pct <= -self.cfg.daily_loss_halt_pct:
            logger.error(f"Daily loss halt {nav.pnl_pct:.2%}, AI skips")
            b.broker.log_operation("ai:decide", {"symbols": symbols, "mode": mode},
                                   False, {"skipped": "daily-loss-halt",
                                           "pnl_pct": round(nav.pnl_pct, 4)},
                                   None, None)
            return {"ok": False, "error": f"日亏熔断 {nav.pnl_pct:.2%}", "timestamp": now}

        # 5. 参考信号 + 上下文，问 LLM
        ref = b.strategy.generate_signals(all_bars)
        ref_txt = "无" if not ref else "、".join(
            f"{s.symbol}{'买入' if s.direction.value == 1 else '卖出'}{s.volume}股@{s.price:.2f}"
            for s in ref.values())
        ctx = b.llm_context(max_ops=10, max_nav=3, closes_n=10)
        prompt = USER_TMPL.format(
            pool="、".join(symbols),
            max_orders=self.cfg.max_orders_per_run,
            max_order_value=int(self.cfg.max_order_value),
            signals=ref_txt,
            context=_json.dumps(ctx, ensure_ascii=False))
        system = TRADER_SYSTEM.format(
            max_order_value=int(self.cfg.max_order_value),
            max_orders=self.cfg.max_orders_per_run)
        from paper_trading.llm import LLMError
        from paper_trading.llm.provider import chat as _chat

        try:
            cfg = b._llm_config()
            raw = _chat(cfg, [{"role": "user", "content": prompt}], system=system)
        except LLMError as e:
            b.broker.log_operation("ai:decide", {"symbols": symbols, "mode": mode},
                                   False, {"error": str(e)}, None, None)
            return {"ok": False, "error": str(e), "timestamp": now}

        # 6. 解析（坏 JSON 直接作废）
        plan = _extract_json(raw or "")
        actions = (plan or {}).get("actions", []) if isinstance(plan, dict) else []
        if not isinstance(actions, list):
            actions = []
        summary = str((plan or {}).get("summary", ""))[:200] if plan else ""

        # 7. 逐条钳制执行
        from paper_trading.models import Signal, SignalType

        allow = set(symbols)
        decided: list[dict] = []
        n_orders = 0
        for a in actions[: self.cfg.max_orders_per_run + 5]:  # 多出的只记拒绝
            if not isinstance(a, dict):
                continue
            act = str(a.get("action", "hold")).lower()
            sym = str(a.get("symbol", ""))
            reason = str(a.get("reason", ""))[:120]
            rec: dict[str, Any] = {"action": act, "symbol": sym,
                                   "volume": a.get("volume"), "reason": reason}
            if act == "hold" or not sym:
                rec["status"] = "持有不动" if act == "hold" else "拒绝：无代码"
                decided.append(rec)
                continue
            try:
                vol = int(a.get("volume") or 0)
            except (TypeError, ValueError):
                vol = 0
            px = latest.get(sym, 0.0)
            if sym not in allow:
                rec["status"] = "拒绝：不在股票池"
            elif vol <= 0 or vol % 100 != 0:
                rec["status"] = "拒绝：数量须为100倍数"
            elif vol * px > self.cfg.max_order_value:
                rec["status"] = f"拒绝：超单笔上限{int(self.cfg.max_order_value)}元"
            elif n_orders >= self.cfg.max_orders_per_run:
                rec["status"] = "拒绝：超日内笔数上限"
            else:
                sig = Signal(symbol=sym,
                             direction=(SignalType.BUY if act == "buy" else SignalType.SELL),
                             volume=vol, price=px, reason="ai:" + reason)
                if act not in ("buy", "sell"):
                    rec["status"] = "拒绝：未知动作"
                else:
                    ok, why = b.risk.check_signal(sig, px, cash, positions,
                                                 nav.total_value, prices=latest)
                    if not ok:
                        rec["status"] = f"风控拒绝：{why}"
                    elif dry_run:
                        rec["status"] = "试运行通过（未下单）"
                        n_orders += 1
                    else:
                        bars = all_bars.get(sym, [])
                        prev = bars[-2].close if len(bars) >= 2 else None
                        order = Order(symbol=sym,
                                      direction=sig.direction.value, volume=vol,
                                      order_type=OrderType.LIMIT, limit_price=px,
                                      prev_close=prev,
                                      ref_high=bars[-1].high if bars else None,
                                      ref_low=bars[-1].low if bars else None)
                        res = b.broker.submit_order(order)
                        rec["status"] = ("已成交 @%.2f" % res.filled_price
                                         if res.status.value == "filled"
                                         else f"撮合拒绝：{res.status.value}")
                        rec["filled_price"] = res.filled_price
                        n_orders += 1
                        # 刷新资金/持仓快照，供下一单校验
                        cash = b.broker.get_cash()
                        positions = {p.symbol: p for p in b.broker.get_all_positions()}
            decided.append(rec)

        if not dry_run:
            b.portfolio.record_nav(latest)
        nav2 = b.broker.get_nav(latest)
        b.broker.log_operation(
            "ai:decide", {"symbols": symbols, "mode": mode,
                          "summary": summary}, True,
            {"decisions": decided}, nav2.cash, nav2.total_value)
        logger.info(f"AI decide done: {len(decided)} actions, mode={mode}")
        return {"ok": True, "timestamp": now, "symbols": symbols, "mode": mode,
                "summary": summary, "decisions": decided,
                "nav": nav2.__dict__}
