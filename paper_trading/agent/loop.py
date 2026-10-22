"""AI 交易员（P2：日内一次决策，定时主动跑）。

与 TradingBridge.run_daily（MA 规则）互斥：同一账户同一天只跑其一，
由 cron 二选一。本循环自带“今日已决策”幂等闸。
"""
from __future__ import annotations

import json as _json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from paper_trading.cli import TradingBridge

from paper_trading.agent.prompts import TRADER_SYSTEM, USER_TMPL


@dataclass
class AgentConfig:
    enabled: bool = True
    max_orders_per_run: int = 3
    max_order_value: float = 20000.0
    daily_loss_halt_pct: float = 0.05  # 累计浮亏超此比例，当天停手
    llm_timeout: float = 120.0  # 决策上下文大，放宽单次超时
    llm_retries: int = 1  # 仅超时重试一次，其他错误直接失败
    llm_max_tokens: int = 8192  # 答案区预算（实测推理 3000+ thinking 会挤掉答案）
    llm_thinking: str = "disabled"  # 格式受限决策默认关 thinking，又快又省又稳
    picks_enabled: bool = True   # AI 自主提名池外标的（候选之外也允许它选股）
    picks_max: int = 5           # 单轮最多提名多少只（1-10）
    # AI 条件单：LLM 只出触发条件，引擎盘中每分钟执行（实时层）
    conditions_enabled: bool = True
    max_conditions: int = 5
    condition_max_days: int = 3
    # 事件驱动复盘：只有异动才叫 LLM，避免按分钟空跑
    event_enabled: bool = True
    event_price_move_pct: float = 3.0
    event_drawdown_pct: float = 5.0
    event_cooldown_min: int = 30
    event_min_interval_min: int = 15
    event_max_calls_per_day: int = 6
    event_max_conditions: int = 3


def _ask_llm(cfg, prompt: str, system: str, timeout: float, retries: int,
             max_tokens: int = 8192, thinking: str = "disabled") -> str:
    """带超时重试的 LLM 调用（只重试超时；Key 不进错误信息由 provider 保证）。

    thinking="disabled" 时透传厂商开关，关掉 chain-of-thought：
    格式受限的 JSON 决策不需要思考过程，开着只会烧掉输出预算
    （曾实测 3220 thinking tokens 挤掉答案导致空返回）。

    但有些模型**始终思考**（GLM 5.3 系列，HTTP 400 / 1210 "不支持关闭思考"），
    这时降到 low 档（思考预算最小）再试一次——不能因为一个透传参数把整条链路打死。
    """
    from paper_trading.llm.provider import LLMError
    from paper_trading.llm.provider import chat as _chat

    cfg.timeout = timeout
    cfg.max_tokens = max_tokens

    def _attempt(extra: dict) -> str:
        cfg.extra_body = extra
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return _chat(cfg, [{"role": "user", "content": prompt}],
                             system=system)
            except Exception as e:  # noqa: BLE001 - 仅超时重试，其余直接抛
                last = e
                if "timed out" not in str(e).lower() \
                        and "timeout" not in str(e).lower():
                    raise
                if attempt >= retries:
                    raise
        raise last or RuntimeError("LLM unreachable")

    extra = {"thinking": {"type": "disabled"}} if thinking == "disabled" else {}
    try:
        return _attempt(extra)
    except LLMError as e:
        if thinking == "disabled" and "1210" in str(e):
            # 始终思考的模型：思考也占输出预算，给足（至少 4096），
            # 否则"只剩思考、没有答案"（真机在 2048 上撞过）
            cfg.max_tokens = max(int(max_tokens or 0), 4096)
            return _attempt({"thinking": {"type": "enabled", "level": "low"}})
        raise


def today_str() -> str:
    """今日日期 YYYY-MM-DD（计划/幂等键用）。"""
    return datetime.now().date().isoformat()


def _extract_json(text: str) -> Optional[dict]:
    """从 LLM 输出中提取第一个 {...} 并解析，失败返回 None（fail-closed）。"""
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        obj = _json.loads(text[start:end])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def parse_picks(raw: Optional[str], cap: int = 5) -> list[dict]:
    """解析 LLM 提名（pass1）：只取 query 字段，脏输出/坏 JSON 一律返回 []。"""
    obj = _extract_json(raw or "")
    if not obj:
        return []
    picks = obj.get("picks")
    if not isinstance(picks, list):
        return []
    out: list[dict] = []
    for p in picks:
        if isinstance(p, str):
            q, reason = p, ""
        elif isinstance(p, dict):
            q, reason = p.get("query") or p.get("name") or p.get("symbol") or "", \
                str(p.get("reason") or "")[:120]
        else:
            continue
        q = str(q).strip()
        if q:
            out.append({"query": q, "reason": reason})
        if len(out) >= max(1, cap):
            break
    return out


class AgentTrader:
    """日内一次的 AI 决策循环。"""

    def __init__(self, bridge: "TradingBridge", cfg: Optional[AgentConfig] = None) -> None:
        self.bridge = bridge
        self.cfg = cfg or AgentConfig()

    def _today_ops(self) -> list[dict]:
        """今日全量流水（按日期查，不用最近 N 条窗口——窗口会被面板问答挤爆）。"""
        return self.bridge.broker.get_op_log_day(datetime.now().date().isoformat())

    def _live_runs_today(self) -> list[str]:
        """今日实盘实质决策时刻表（HH:MM；试运行/dry 不计入，不消耗定时次数）。"""
        today = datetime.now().date().isoformat()
        out: list[str] = []
        for o in self._today_ops():
            if o["action"] != "ai:decide" or not o["ok"]:
                continue
            if str(o["timestamp"])[:10] != today:
                continue
            try:
                import json as _json

                res = _json.loads(o["result"] or "{}")
                prm = _json.loads(o["params"] or "{}")
            except Exception:
                res, prm = {}, {}
            if "skipped" in res or prm.get("mode", "live") == "dry":
                continue
            out.append(str(o["timestamp"])[11:16])
        return sorted(out)

    def _fired_entries_today(self, entries: list[dict]) -> set[str]:
        """今日已消费时刻：凭各动作流水里的 slot 标记（与时钟无关）。

        - trade/analyze：ai:decide 实质流水的 slot（dry 照样消费自己的那条）
        - plan：ai:plan 成功流水的 slot；sync：sync 成功流水的 slot
        - 无标记的手动跑/老流水不消费队列；升级前的无标记实盘用时间回退推断
        """
        from paper_trading.agent import schedule as _sched

        today = datetime.now().date().isoformat()
        done: set[str] = set()
        legacy_times: list[str] = []
        for o in self._today_ops():
            if not o["ok"]:
                continue
            if str(o["timestamp"])[:10] != today:
                continue
            try:
                import json as _json

                res = _json.loads(o["result"] or "{}")
                prm = _json.loads(o["params"] or "{}")
            except Exception:
                res, prm = {}, {}
            act = o["action"]
            if act == "ai:decide":
                if "skipped" in res:
                    continue
                mk = str(prm.get("slot") or "")
                if mk:
                    done.add(mk)
                elif prm.get("mode", "live") != "dry":
                    legacy_times.append(str(o["timestamp"])[11:16])
            elif act in ("ai:plan", "sync"):
                if res.get("plan_id") is not None or act == "sync":
                    mk = str(prm.get("slot") or "")
                    if mk:
                        done.add(mk)
        times = [e["time"] for e in entries]
        return {t for t in done if t in times} | _sched.consumed_slots(times, legacy_times)

    def _decided_today(self) -> bool:
        """今日是否已有实质决策（跳过类流水不算，避免早盘空跑锁死午后真跑）。"""
        return len(self._live_runs_today()) > 0

    def _scheme_txt(self) -> str:
        from paper_trading.agent.prompts import SCHEME_TMPL

        s = self.bridge.scheme
        txt = SCHEME_TMPL.format(title=s.title or s.name, name=s.name,
                                 desc=s.desc or "（无说明）",
                                 exits=s.exits_note or "MA 死叉离场")
        if s.name == "custom" and (s.instruction or "").strip():
            txt += ("\n用户自定义交易指令（最高优先级，风控与合规除外）：\n"
                    + s.instruction.strip()[:2000])
        return txt

    def _cand_txt(self, candidates: Optional[list]) -> str:
        if not candidates:
            return ""
        from paper_trading.agent.prompts import CAND_TMPL

        lines = []
        for c in (candidates or [])[:20]:
            if not isinstance(c, dict):
                continue
            lines.append(
                f"{c.get('name', '')}({c.get('symbol', '')}) "
                f"score={c.get('score')} PE={c.get('pe')} "
                f"理由：{str(c.get('reason') or '')[:80]}")
        return CAND_TMPL.format(cand_lines="\n".join(lines)) if lines else ""

    def _picks_txt(self, picks: list[dict]) -> str:
        if not picks:
            return ""
        from paper_trading.agent.prompts import PICK_TMPL

        lines = [f"- {p.get('name') or p['symbol']}({p['symbol']})：{p.get('reason') or '（未给理由）'}"
                 for p in picks]
        return PICK_TMPL.format(pick_lines="\n".join(lines))

    def _pick_extra(self, symbols: list[str], candidates: Optional[list],
                    enable: bool = True) -> list[dict]:
        """AI 自主提名（pass1）：让它在全市场里点名想研究的股票。

        流程：LLM 提名 → 程序用母库全市场代码表解析/校验 → 只留合法 A 股。
        任何失败（LLM 坏 JSON / 库不可读 / 全不合法）都返回 []，绝不阻断决策。
        """
        from paper_trading.agent.prompts import PICK_SYSTEM, PICK_USER
        from paper_trading.integration import search_symbols
        from paper_trading.llm import LLMError
        from paper_trading.utils import get_logger

        logger = get_logger(__name__)
        if not (enable and self.cfg.picks_enabled):
            return []
        b = self.bridge
        try:
            cfg = b._llm_config()
            ctx = b.llm_context(max_ops=3, max_nav=2, closes_n=3)
            hold = ["无（空仓）"] if not ctx.get("positions") else [
                f"{p['stock']} {p['total']}股@{p['avg_cost']}" for p in ctx["positions"]]
            cand_lines = "\n".join(
                f"- {c.get('name')}({c.get('symbol')}) score={c.get('score')} "
                f"PE={c.get('pe')}" for c in (candidates or [])[:20]) or "（本次无程序候选）"
            prompt = PICK_USER.format(
                n=len(candidates or []), cand_lines=cand_lines,
                scheme=self._scheme_txt(), holdings="\n".join(hold),
                context=_json.dumps(ctx, ensure_ascii=False)[:3000],
                max_picks=self.cfg.picks_max)
            system = PICK_SYSTEM.format(max_picks=self.cfg.picks_max)
            raw = _ask_llm(cfg, prompt, system, self.cfg.llm_timeout,
                           self.cfg.llm_retries, min(2048, self.cfg.llm_max_tokens),
                           self.cfg.llm_thinking)
        except (LLMError, Exception) as e:   # noqa: BLE001 - 提名失败不阻断
            logger.warning(f"AI picks skipped: {type(e).__name__}: {e}")
            return []
        wanted = parse_picks(raw, self.cfg.picks_max)
        if not wanted:
            return []
        found = search_symbols([w["query"] for w in wanted],
                               local_names=b.data_db.get_stock_names())
        reason_by_q = {w["query"]: w.get("reason", "") for w in wanted}
        out: list[dict] = []
        for hit in found:
            if hit["symbol"] in symbols:
                continue
            rec = dict(hit)
            rec["reason"] = reason_by_q.get(hit.get("query", ""), "")
            out.append(rec)
        logger.info(f"AI picks resolved: {[(p['symbol'], p.get('name')) for p in out]}")
        return out

    def _enrich(self, symbols: list[str], candidates: Optional[list],
                enable: bool = True) -> tuple[list[str], list[dict]]:
        """候选池 + AI 提名 = 本轮决策范围；提名股票同步补数据。"""
        picks = self._pick_extra(symbols, candidates, enable)
        if not picks:
            return list(symbols), []
        merged = list(symbols) + [p["symbol"] for p in picks]
        try:
            self.bridge.sync_data([p["symbol"] for p in picks])
        except Exception:
            pass   # 补数据失败：决策阶段若无 K 线会被新鲜守卫拦下，安全
        return merged, picks

    def _decision_symbols(self, symbols: list[str]) -> list[str]:
        """本轮决策范围 = 传入池 ∪ 全部持仓（持仓即使退出筛选池也必须可见）。

        只把持仓写进上下文是不够的：旧实现执行时用 `symbols` 做白名单，
        池外持仓的卖出会被“不在股票池”拒绝。这里从入口统一并入，
        后续 prompt / 价格 / 白名单才与真实账户一致。
        """
        held = [p.symbol for p in self.bridge.broker.get_all_positions()]
        return list(dict.fromkeys([*(symbols or []), *held]))

    def _engine_min_ticket(self) -> float:
        """条件单/引擎共用的手续费地板（读 config，缺省 0 仅测试兜底）。"""
        try:
            from paper_trading.engine.config import load_engine_config

            cfg = load_engine_config(getattr(self.bridge, "config_path", "config.yaml"))
            return float(cfg.get("min_ticket_cny") or 0.0)
        except Exception:                      # noqa: BLE001 - 配置缺失不阻断决策
            return 0.0

    def _conditions_from_raw(self, raw: Optional[str],
                             pool: list[str]) -> tuple[list[dict], list[dict]]:
        """从决策 JSON 里取 conditions，返回 (合法条目, 被拒原因)。

        坏输出/池外/金额越界不能静默消失；原因要进 ai:decide / ai:plan 流水。
        """
        if not self.cfg.conditions_enabled:
            return [], []
        from paper_trading.engine.ai_conditions import parse_conditions_verbose

        try:
            return parse_conditions_verbose(
                raw, pool,
                max_conds=int(self.cfg.max_conditions),
                max_value=float(self.cfg.max_order_value),
                min_ticket=self._engine_min_ticket())
        except Exception as e:                # noqa: BLE001 - 条件单失败不阻断主决策
            return [], [{"why": f"解析异常：{type(e).__name__}: {e}"}]

    def _arm_conditions(self, conditions: list, *, actions: Optional[list] = None,
                        dry_run: bool = False) -> dict:
        """校验并挂条件单；与直接动作/已有条件重复的条目跳过。

        这里不负责成交。真正的盘中触发由 ConditionRunner 每分钟完成，
        与规则引擎共用风控、费用和日内预算。
        """
        empty = {"created": [], "rejected": [], "skipped": []}
        if dry_run or not conditions:
            return empty
        try:
            from paper_trading.engine.conditional import ConditionalOrders

            orders = ConditionalOrders(
                getattr(self.bridge, "account_db_path", "paper_account.db"))
            direct = {
                (str(a.get("symbol") or ""), str(a.get("action") or "").lower())
                for a in (actions or []) if isinstance(a, dict)
            }
            # 同一标的同方向只要已有 pending（不论触发价/人工或 AI），就不再叠一条：
            # 两条都触发会变成双倍下单，宁可少挂也不重复。
            seen = {
                (str(d.get("symbol") or ""), str(d.get("side") or ""))
                for d in orders.pending()
            }
            filtered: list[dict] = []
            skipped: list[dict] = []
            for c in conditions:
                if not isinstance(c, dict):
                    continue
                sym = str(c.get("symbol") or "")
                side = str(c.get("side") or "")
                if (sym, side) in direct:
                    skipped.append({"symbol": sym, "side": side,
                                    "why": "与直接动作重复，避免双重下单"})
                    continue
                if (sym, side) in seen:
                    skipped.append({"symbol": sym, "side": side,
                                    "why": "同方向条件单已存在，避免双重下单"})
                    continue
                seen.add((sym, side))
                filtered.append(c)
            res = orders.create_many(filtered, min_ticket=self._engine_min_ticket())
            res["skipped"] = skipped
            return res
        except Exception as e:                # noqa: BLE001 - 条件单故障不能回滚已成交动作
            return {**empty, "error": f"{type(e).__name__}: {e}"}

    def review_conditions(self, symbols: list[str], *, event_txt: str = "",
                          dry_run: bool = False,
                          max_conds: Optional[int] = None) -> dict:
        """事件驱动复盘：只让 AI 产出/更新条件单，不直接下单。

        与每日决策共用同一套 prompt 校验和条件单仓库；盘中触发仍由引擎完成。
        这样异动时 AI 可以介入，但不会变成逐笔 LLM 调用。
        """
        from paper_trading.data import realtime as rt
        from paper_trading.engine.ai_conditions import (build_prompt,
                                                         parse_conditions_verbose)

        b = self.bridge
        pool = self._decision_symbols(symbols)[:20]
        if not pool:
            return {"ok": False, "error": "无股票池/持仓，无法复盘"}
        try:
            quotes = rt.get_quotes(pool, force=True) or {}
        except Exception:                      # noqa: BLE001 - 无实时价时仍可用收盘上下文
            quotes = {}
        ctx = b.llm_context(max_ops=3, max_nav=2, closes_n=3, symbols=pool)
        min_ticket = self._engine_min_ticket()
        max_value = float(self.cfg.max_order_value)
        sys_p, user_p = build_prompt(
            pool, quotes, ctx.get("positions", []), self._scheme_txt(),
            str(ctx.get("account", {}).get("data_asof") or ""),
            max_conds=int(max_conds or self.cfg.event_max_conditions),
            max_days=int(self.cfg.condition_max_days), event_txt=event_txt,
            min_ticket=min_ticket, max_value=max_value)
        from paper_trading.llm import LLMError

        try:
            raw = _ask_llm(b._llm_config(), user_p, sys_p,
                           self.cfg.llm_timeout, self.cfg.llm_retries,
                           self.cfg.llm_max_tokens, self.cfg.llm_thinking)
        except LLMError as e:
            b.broker.log_operation("ai:review", {"pool": pool, "event": event_txt[:300]},
                                   False, {"error": str(e)}, None, None)
            return {"ok": False, "error": str(e), "pool": pool}
        conds, parse_rejected = parse_conditions_verbose(
            raw, pool, max_conds=int(max_conds or self.cfg.event_max_conditions),
            max_value=max_value, min_ticket=min_ticket)
        res = self._arm_conditions(conds, dry_run=dry_run)
        b.broker.log_operation(
            "ai:review", {"pool": pool, "event": event_txt[:300],
                          "dry_run": bool(dry_run)}, True,
            {"parsed": len(conds), "parse_rejected": parse_rejected,
             **res, "raw": str(raw or "")[:800]}, None, None)
        return {"ok": True, "pool": pool, "parsed": len(conds),
                "parse_rejected": parse_rejected, **res,
                "raw": str(raw or "")[:600]}

    def _plan(self, symbols: list[str], candidates: Optional[list] = None,
              pool_source: str = "config",
              slot: Optional[str] = None) -> dict:
        """休盘做计划：基于最新已收盘定稿数据问 LLM，存待执行，不碰账本。"""
        from paper_trading.utils import get_logger

        logger = get_logger(__name__)
        b = self.bridge
        now = datetime.now().isoformat()

        # 持仓即使不在本轮筛选池，也必须在 AI 决策范围内（否则无法卖）。
        symbols = self._decision_symbols(symbols)
        b.sync_data(symbols)
        symbols, picks = self._enrich(symbols, candidates)
        all_bars: dict[str, list] = {}
        for sym in symbols:
            bars = b.data_db.get_bars(sym, limit=30)
            if bars:
                all_bars[sym] = bars
        if not all_bars:
            return {"ok": False, "error": "无历史数据，无法做计划", "timestamp": now}
        asof = max(bars[-1].timestamp.date().isoformat() for bars in all_bars.values())

        ref = b.strategy.generate_signals(all_bars)
        ref_txt = "无" if not ref else "、".join(
            f"{s.symbol}{'买入' if s.direction.value == 1 else '卖出'}{s.volume}股@{s.price:.2f}"
            for s in ref.values())
        ctx = b.llm_context(max_ops=10, max_nav=3, closes_n=10, symbols=symbols)
        prompt = USER_TMPL.format(
            pool="、".join(symbols),
            max_orders=self.cfg.max_orders_per_run,
            max_order_value=int(self.cfg.max_order_value),
            candidates=self._cand_txt(candidates)
            + ("\n" + self._picks_txt(picks) if picks else ""),
            signals=ref_txt,
            scheme=self._scheme_txt(),
            context=_json.dumps(ctx, ensure_ascii=False))
        system = (TRADER_SYSTEM.format(
            max_order_value=int(self.cfg.max_order_value),
            max_orders=self.cfg.max_orders_per_run,
            max_conditions=int(self.cfg.max_conditions),
            condition_max_days=int(self.cfg.condition_max_days),
            min_ticket=int(self._engine_min_ticket()))
            + f"\n注意：当前为休盘，最新定稿数据截至 {asof}。"
              "你的决策将在下一个开盘执行。")
        from paper_trading.llm import LLMError

        try:
            cfg = b._llm_config()
            raw = _ask_llm(cfg, prompt, system,
                           self.cfg.llm_timeout, self.cfg.llm_retries,
                           self.cfg.llm_max_tokens, self.cfg.llm_thinking)
        except LLMError as e:
            b.broker.log_operation("ai:plan", {"symbols": symbols, "pool_source": pool_source}, False,
                                   {"error": str(e)}, None, None)
            return {"ok": False, "error": str(e), "timestamp": now}
        plan = _extract_json(raw or "")
        actions = (plan or {}).get("actions", []) if isinstance(plan, dict) else []
        if not isinstance(actions, list):
            return {"ok": False, "error": "LLM 输出非 JSON 计划，已作废",
                    "timestamp": now, "symbols": symbols}
        summary = str((plan or {}).get("summary", ""))[:200]
        conditions, conditions_rejected = self._conditions_from_raw(raw, symbols)
        # 计划基准价：买/卖动作一律回填定稿收盘价（prompt 约定 null=最新收盘）。
        # 不采信 LLM 自报价格；带 price_source 的基准价才允许在执行缺价时兜底。
        for a in actions:
            if not isinstance(a, dict):
                continue
            if str(a.get("action", "")).lower() not in ("buy", "sell"):
                continue
            bars = all_bars.get(str(a.get("symbol", ""))) or []
            if not bars:
                continue
            try:
                a["price"] = round(float(bars[-1].close), 2)
                a["price_source"] = "last_close"
            except (TypeError, ValueError):
                continue
        # 计划是给“下一个交易日开盘”用的；当日 17:15 的 trade 不再消费它。
        from paper_trading.utils.trading_calendar import next_trading_day

        try:
            _holidays = b.broker._holidays()
        except Exception:                      # noqa: BLE001 - 日历不可用按周末跳过
            _holidays = set()
        plan_date = next_trading_day(datetime.now(), _holidays).isoformat()
        pid = b.broker.save_plan(plan_date, symbols,
                                 {"actions": actions, "summary": summary, "asof": asof,
                                  "conditions": conditions,
                                  "conditions_rejected": conditions_rejected,
                                  "pool_source": pool_source,
                                  "picks": [{k: p.get(k) for k in ("symbol", "name", "reason")}
                                            for p in picks]})
        b.broker.log_operation("ai:plan", {"symbols": symbols, "asof": asof,
                                           "plan_date": plan_date,
                                           "pool_source": pool_source,
                                           "n_picks": len(picks),
                                           "n_conditions": len(conditions),
                                           "n_conditions_rejected": len(conditions_rejected),
                                           **({"slot": slot} if slot else {})}, True,
                               {"plan_id": pid, "summary": summary,
                                "n_actions": len(actions),
                                "n_conditions": len(conditions),
                                "conditions_rejected": conditions_rejected,
                                "picks": [{"symbol": p["symbol"],
                                           "name": p.get("name", "")} for p in picks],
                                "raw": str(raw or "")[:1500]}, None, None)
        logger.info(f"AI plan saved id={pid} for={plan_date} asof={asof}")
        return {"ok": True, "mode": "plan", "plan_id": pid, "plan_date": plan_date,
                "asof": asof, "timestamp": now, "symbols": symbols,
                "pool_source": pool_source, "actions": actions,
                "conditions": conditions,
                "conditions_rejected": conditions_rejected,
                "summary": summary}

    def tick(self, symbols: list[str], candidates: Optional[list] = None,
             pool_source: str = "config", force: bool = False,
             sched_root=None) -> dict:
        """队列 tick（供 cron 高频调用）：消费最早已到未跑的条目并按动作执行。

        - sync：只同步行情+名称，记 sync 流水（含 slot 标记）
        - analyze：完整决策链试运行，不下单（ai:decide mode=dry，含 slot 标记）
        - plan：休盘做计划存着（ai:plan 成功流水含 slot 标记）
        - trade：实盘决策（走 run，含 slot 标记）
        无事可做记 not-in-schedule 跳过。手动跑（无 slot 标记）不消费队列。
        """
        from paper_trading.agent import schedule as _sched
        from paper_trading.utils import get_logger

        logger = get_logger(__name__)
        b = self.bridge
        now = datetime.now().isoformat()
        root = sched_root if sched_root is not None else "."
        sc = _sched.load_schedule(root)
        entries = sc["entries"]
        fired = self._fired_entries_today(entries)
        entry = None
        if force:
            due = _sched.due_entries(entries)
            entry = due[0] if due else None
        else:
            _ok, entry, _reason = _sched.check(entries, fired)
        if entry is None:
            logger.warning("Schedule queue empty, tick skips")
            b.broker.log_operation("ai:decide", {"symbols": symbols, "mode": "live"},
                                   True, {"skipped": "not-in-schedule",
                                          "queue": [(e["time"], e["action"])
                                                    for e in entries]}, None, None)
            return {"ok": True, "skipped": "not-in-schedule",
                    "queue": entries, "timestamp": now, "symbols": symbols}
        tm, act = entry["time"], entry["action"]
        logger.info(f"Schedule tick fires {tm}={act}")
        if act == "sync":
            res = b.sync_data(symbols)
            b.broker.log_operation("sync", {"symbols": symbols, "slot": tm},
                                   res.get("ok", False),
                                   {"updated": res.get("updated")}, None, None)
            res["slot"] = tm
            return res
        if act == "plan":
            res = self._plan(symbols, candidates=candidates,
                             pool_source=pool_source, slot=tm)
            res["slot"] = tm
            return res
        if act == "analyze":
            res = self.run(symbols, dry_run=True, candidates=candidates,
                           pool_source=pool_source, ignore_schedule=True,
                           sched_root=root, slot=tm)
            res["slot"] = tm
            return res
        res = self.run(symbols, candidates=candidates, pool_source=pool_source,
                       ignore_schedule=True, sched_root=root, slot=tm)
        res["slot"] = tm
        return res

    def run(self, symbols: list[str], dry_run: bool = False,
            force: bool = False, plan_only: bool = False,
            candidates: Optional[list] = None,
            pool_source: str = "config",
            ignore_schedule: bool = False,
            sched_root=None,
            slot: Optional[str] = None) -> dict:
        """三态：plan_only 只做计划存着；默认先执行今日待执行计划，无则现决现执。

        直接调用 = 跑一次 trade，按 trade 队列过闸；tick() 按动作分发，
        传入 slot 表示已过闸。dry-run 与 plan_only 不受定时闸限制。
        """
        from paper_trading.utils import get_logger

        logger = get_logger(__name__)
        b = self.bridge
        now = datetime.now().isoformat()
        mode = "dry" if dry_run else "live"

        if plan_only:
            return self._plan(symbols, candidates=candidates, pool_source=pool_source)

        # 0. 定时闸（直接 run = 跑一次 trade：只看 trade 条目队列）。
        #    单 trade 条目沿用历史 already-decided 语义。
        #    放行后记住本次消费时刻，落子时写入流水（消费凭标记，不凭时间推断）。
        #    tick() 传入 slot 表示已过闸，不重复判定。
        _slot_s: Optional[str] = slot
        if _slot_s is None and not dry_run and not force and not plan_only:
            from paper_trading.agent import schedule as _sched

            _sc = _sched.load_schedule(sched_root if sched_root is not None else ".")
            _trades = [e for e in _sc["entries"] if e["action"] == "trade"]
            _live = self._live_runs_today()
            _fired = self._fired_entries_today(_sc["entries"])
            if _live and len(_trades) <= 1:
                logger.warning("AI already decided today, skip (idempotency)")
                b.broker.log_operation("ai:decide", {"symbols": symbols, "mode": mode},
                                       True, {"skipped": "already-decided"}, None, None)
                return {"ok": True, "skipped": "already-decided", "timestamp": now,
                        "symbols": symbols}
            if not ignore_schedule:
                _ok_s, _entry_s, _reason_s = _sched.check(_trades, _fired)
                if not _ok_s:
                    logger.warning(f"Schedule gate ({_reason_s}), AI skips")
                    b.broker.log_operation(
                        "ai:decide", {"symbols": symbols, "mode": mode},
                        True, {"skipped": _reason_s,
                               "queue": _sc["entries"]},
                        None, None)
                    return {"ok": True, "skipped": _reason_s,
                            "queue": _sc["entries"],
                            "timestamp": now, "symbols": symbols}
                _slot_s = _entry_s["time"]

        # 1. 同步行情（与 run 同一口径）；持仓并入决策范围，池外持仓才卖得掉。
        symbols = self._decision_symbols(symbols)
        b.sync_data(symbols)

        # 2. 取数（执行计划不需要新鲜 K 线：计划本就是基于定稿数据做的）
        all_bars: dict[str, list] = {}
        for sym in symbols:
            bars = b.data_db.get_bars(sym, limit=30)
            if bars:
                all_bars[sym] = bars
        # 计价用全口径（run 标的 ∪ 持仓），持仓按 0 算会误触发熔断
        latest = b.price_map(symbols)

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
        dl_halt, dl = b.risk.check_daily_loss(nav.total_value, today_str(),
                                              halt_pct=self.cfg.daily_loss_halt_pct)
        if dl_halt:
            logger.error(f"Daily loss halt {dl:.2%}, AI skips")
            b.broker.log_operation("ai:decide", {"symbols": symbols, "mode": mode},
                                   False, {"skipped": "daily-loss-halt",
                                           "daily_loss": round(dl, 4)},
                                   None, None)
            return {"ok": False, "error": f"日亏熔断 {dl:.2%}", "timestamp": now}

        # 4.5 待执行计划优先（休盘计划开盘执行；dry-run 不消费计划；
        #     计划基于定稿数据，不受新鲜守卫限制）
        if not dry_run:
            # 计划存的是“下一个交易日”；允许消费今天及更早的 pending，
            # 这样 16:45 做的计划不会在 17:15 被同一晚的 trade 提前执行。
            pending = b.broker.get_executable_plan(today_str())
            if pending:
                logger.info(f"Executing pending AI plan {pending['id']}")
                return self._execute_plan(pending, all_bars, latest, slot=_slot_s)

        # 4.6 无新鲜数据守卫（只拦现决现执：节假日/源未更新时不问 LLM）
        today = datetime.now().date()
        if not any(bars and bars[-1].timestamp.date() >= today for bars in all_bars.values()):
            _asof = ""
            try:
                _asof = max(bars[-1].timestamp for bars in all_bars.values()
                            if bars).date().isoformat()
            except Exception:
                _asof = ""
            logger.warning(f"No fresh bars (latest {_asof or 'none'}), AI skips")
            b.broker.log_operation("ai:decide", {"symbols": symbols, "mode": mode},
                                   True, {"skipped": "no-fresh-bars",
                                          "latest": _asof}, None, None)
            return {"ok": True, "skipped": "no-fresh-bars", "latest": _asof,
                    "timestamp": now, "symbols": symbols}

        # 4.7 AI 自主提名（pass1）：候选之外允许它点名全市场任意 A 股。
        #     放在新鲜守卫之后——没新行情就不必白花一次 LLM 与补数。
        #     有待执行计划时不提名（按计划原样执行，不临时扩池）。
        picks: list[dict] = []
        if not plan_only and not b.broker.get_executable_plan(today_str()):
            symbols, picks = self._enrich(symbols, candidates)
            if picks:
                for sym in (p["symbol"] for p in picks):
                    bars = b.data_db.get_bars(sym, limit=30)
                    if bars:
                        all_bars[sym] = bars
                latest = b.price_map(symbols)

        # 5. 参考信号 + 上下文，问 LLM
        ref = b.strategy.generate_signals(all_bars)
        ref_txt = "无" if not ref else "、".join(
            f"{s.symbol}{'买入' if s.direction.value == 1 else '卖出'}{s.volume}股@{s.price:.2f}"
            for s in ref.values())
        ctx = b.llm_context(max_ops=10, max_nav=3, closes_n=10, symbols=symbols)
        prompt = USER_TMPL.format(
            pool="、".join(symbols),
            max_orders=self.cfg.max_orders_per_run,
            max_order_value=int(self.cfg.max_order_value),
            candidates=self._cand_txt(candidates)
            + ("\n" + self._picks_txt(picks) if picks else ""),
            signals=ref_txt,
            scheme=self._scheme_txt(),
            context=_json.dumps(ctx, ensure_ascii=False))
        system = TRADER_SYSTEM.format(
            max_order_value=int(self.cfg.max_order_value),
            max_orders=self.cfg.max_orders_per_run,
            max_conditions=int(self.cfg.max_conditions),
            condition_max_days=int(self.cfg.condition_max_days),
            min_ticket=int(self._engine_min_ticket()))
        if picks:
            # AI 提名的股票已由程序校验并补齐数据，可直接决策（仍在风控钳制内）
            system += ("\n本轮额外纳入以下 AI 自主提名标的（程序已校验为合法 A 股并补齐数据）："
                       + "、".join(f"{p.get('name') or p['symbol']}({p['symbol']})"
                                   for p in picks))
        from paper_trading.llm import LLMError

        try:
            cfg = b._llm_config()
            raw = _ask_llm(cfg, prompt, system,
                           self.cfg.llm_timeout, self.cfg.llm_retries,
                           self.cfg.llm_max_tokens, self.cfg.llm_thinking)
        except LLMError as e:
            b.broker.log_operation("ai:decide", {"symbols": symbols, "pool_source": pool_source,
                                                 "mode": mode, "n_picks": len(picks)},
                                   False, {"error": str(e)}, None, None)
            return {"ok": False, "error": str(e), "timestamp": now}

        # 6. 解析（坏 JSON 直接作废）
        plan = _extract_json(raw or "")
        actions = (plan or {}).get("actions", []) if isinstance(plan, dict) else []
        if not isinstance(actions, list):
            actions = []
        summary = str((plan or {}).get("summary", ""))[:200] if plan else ""
        conditions, conditions_rejected = self._conditions_from_raw(raw, symbols)

        # 7. 逐条钳制执行（现决与计划执行共用实现）
        return self._execute_actions(
            actions=actions, summary=summary, symbols=symbols,
            all_bars=all_bars, latest_prices=latest, cash=cash,
            positions=positions, nav_total=nav.total_value,
            dry_run=dry_run, mode=mode, now=now, from_plan=False,
            pool_source=pool_source, slot=_slot_s,
            picks=[{"symbol": p["symbol"], "name": p.get("name", "")}
                   for p in picks], conditions=conditions,
            conditions_rejected=conditions_rejected)

    def _execute_plan(self, pending: dict, all_bars: dict,
                      latest: dict, slot: Optional[str] = None) -> dict:
        """执行今日待执行计划（休盘计划开盘执行；计划标的自动补数，缺价 fail-closed）。"""
        from paper_trading.utils import get_logger

        logger = get_logger(__name__)
        b = self.bridge
        plan = pending.get("plan") or {}
        actions = plan.get("actions", [])
        if not isinstance(actions, list):
            actions = []
        conditions = plan.get("conditions", [])
        if not isinstance(conditions, list):
            conditions = []
        conditions_rejected = plan.get("conditions_rejected", [])
        if not isinstance(conditions_rejected, list):
            conditions_rejected = []
        summary = str(plan.get("summary", ""))[:200]
        syms = [s for s in str(pending.get("symbols", "")).split(",") if s]
        # 计划可能包含本轮运行股票池之外的新增标的（AI 提名等）。
        # 执行上下文必须覆盖计划自身标的，否则 latest 缺价会按 0 参与风控，
        # 单笔金额上限被绕过，最终只得到一句误导性的“无价格”拒单。
        all_bars = dict(all_bars)
        latest = dict(latest)
        positions = {p.symbol: p for p in b.broker.get_all_positions()}
        for sym in set(syms) | set(positions):
            bars = all_bars.get(sym)
            if not bars:
                bars = b.data_db.get_bars(sym, limit=30)
                if bars:
                    all_bars[sym] = bars
            if not latest.get(sym) and bars:
                latest[sym] = float(bars[-1].close)
        cash = b.broker.get_cash()
        nav = b.broker.get_nav(latest)
        res = self._execute_actions(
            actions=actions, summary=summary, symbols=syms,
            all_bars=all_bars, latest_prices=latest, cash=cash,
            positions=positions, nav_total=nav.total_value,
            dry_run=False, mode="live", now=datetime.now().isoformat(),
            from_plan=True, pool_source=str(plan.get("pool_source", "config")),
            slot=slot, conditions=conditions,
            conditions_rejected=conditions_rejected)
        b.broker.mark_plan_done(int(pending["id"]))
        logger.info(f"AI plan {pending['id']} executed")
        return res

    def _execute_actions(self, *, actions: list, summary: str, symbols: list,
                         all_bars: dict, latest_prices: dict, cash: float,
                         positions: dict, nav_total: float, dry_run: bool,
                         mode: str, now: str, from_plan: bool,
                         pool_source: str = "config",
                         slot: Optional[str] = None,
                         picks: Optional[list] = None,
                         conditions: Optional[list] = None,
                         conditions_rejected: Optional[list] = None) -> dict:
        """逐条钳制执行（dry_run 不碰账本；from_plan 仅标记来源）。"""
        from paper_trading.models import Order, OrderType, Signal, SignalType
        from paper_trading.utils import get_logger

        logger = get_logger(__name__)
        b = self.bridge
        latest = latest_prices
        allow_buy = set(symbols)
        allow_sell = set(positions)
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
                # 跨模型字段名容忍：volume / shares / quantity 都认
                vraw = a.get("volume", a.get("shares", a.get("quantity")))
                vol = int(vraw or 0)
            except (TypeError, ValueError):
                vol = 0
            px = 0.0
            try:
                px = float(latest.get(sym) or 0.0)
            except (TypeError, ValueError):
                px = 0.0
            # 计划价只允许作为“定稿收盘价”来源的缺价兜底；
            # 来源不明的价格不采信，防止低价绕过金额上限。
            if (not math.isfinite(px) or px <= 0) and from_plan \
                    and a.get("price_source") == "last_close":
                try:
                    px = float(a.get("price") or 0.0)
                except (TypeError, ValueError):
                    px = 0.0
            if act not in ("buy", "sell"):
                rec["status"] = "拒绝：未知动作"
            elif act == "buy" and sym not in allow_buy:
                rec["status"] = "拒绝：不在股票池"
            elif act == "sell" and sym not in allow_sell:
                rec["status"] = "拒绝：无可卖持仓"
            elif vol <= 0 or vol % 100 != 0:
                rec["status"] = "拒绝：数量须为100倍数"
            elif not math.isfinite(px) or px <= 0:
                rec["status"] = "拒绝：无有效价格"
            elif vol * px > self.cfg.max_order_value:
                rec["status"] = f"拒绝：超单笔上限{int(self.cfg.max_order_value)}元"
            elif n_orders >= self.cfg.max_orders_per_run:
                rec["status"] = "拒绝：超日内笔数上限"
            else:
                sig = Signal(symbol=sym,
                             direction=(SignalType.BUY if act == "buy" else SignalType.SELL),
                             volume=vol, price=px, reason="ai:" + reason)
                ok, why = b.risk.check_signal(sig, px, cash, positions,
                                             nav_total, prices=latest)
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

        # AI 条件单：现在不成交，交给引擎盘中每分钟盯价；与直接动作重复的跳过。
        cond_res = self._arm_conditions(conditions or [], actions=actions,
                                        dry_run=dry_run)
        cond_res["parse_rejected"] = conditions_rejected or []
        if not dry_run:
            b.portfolio.record_nav(latest)
        nav2 = b.broker.get_nav(latest)
        b.broker.log_operation(
            "ai:decide", {"symbols": symbols, "mode": mode, "from_plan": from_plan,
                          "pool_source": pool_source, "summary": summary,
                          "n_picks": len(picks or []),
                          "n_conditions": len(conditions or []),
                          "n_conditions_rejected": len(conditions_rejected or []),
                          **({"slot": slot} if slot else {})}, True,
            {"decisions": decided, "picks": picks or [], "conditions": cond_res},
            nav2.cash, nav2.total_value)
        logger.info(f"AI decide done: {len(decided)} actions, mode={mode}, from_plan={from_plan}")
        return {"ok": True, "timestamp": now, "symbols": symbols, "mode": mode,
                "from_plan": from_plan, "pool_source": pool_source,
                "summary": summary, "decisions": decided,
                "picks": picks or [], "conditions": cond_res,
                "nav": nav2.__dict__}
