"""AI 条件单设计器：LLM 只出"触发条件 + 有效期 + 理由"，引擎盯盘触发执行。

分工（关键）：
- LLM：今天的判断 → 变成**可验证的条件**（例：宁德时代 跌破 260 买 1 万，
  3 个交易日内有效，理由写清）
- 引擎：盘中每分钟核对实时价，触发即过风控下单；不触发就 0 成本

好处：AI 调用频率可以低（一天 1~2 次），但盘中覆盖是连续的；
且每条条件都留痕（触发/未触发/被风控拒），可复盘 AI 准不准。
"""
from __future__ import annotations

import json as _json
from typing import Optional

COND_RULES = """条件单规则（违反即作废该条）：
1. 只输出 JSON：{"conditions":[{"symbol":"6位代码","side":"buy|sell",
   "cond":{"type":"price_below|price_above|pct_below|pct_above","value":数字},
   "value":买入金额(元,buy必填),"volume":股数(可选),
   "expiry":"YYYY-MM-DD","reason":"一句话理由"}],"summary":"一句话总结"}
2. 条件必须是**可验证的数字**（触发价或涨跌幅%），不许写"跌多了就买"这类模糊话
3. 触发价要与当前价有合理距离（不要贴着现价设条件，那等于立刻成交）
4. 最多 {max_conds} 条；没有看好的机会就输出空数组
5. expiry 最长 {max_days} 天
6. 只用给定股票池里的代码；数量必须 100 的整数倍
7. 全部中文。"""


def build_prompt(pool: list[str], quotes: dict, positions: list[dict],
                 scheme_txt: str, asof: str, max_conds: int = 5,
                 max_days: int = 3) -> tuple[str, str]:
    """构造（system, user）prompt：只给必要上下文，控制 token。"""
    q_lines = []
    for s in pool[:20]:
        q = quotes.get(s) or {}
        px = q.get("price")
        chg = q.get("change_pct")
        if px:
            q_lines.append(f"- {s} 现价 {px}（{chg:+.2f}%）" if chg is not None
                           else f"- {s} 现价 {px}")
    pos_txt = ("、".join(f"{p['stock']}{p['total']}股@{p['avg_cost']}"
                          for p in positions) or "空仓")
    user = (f"数据截至 {asof}\n股票池实时价：\n" + ("\n".join(q_lines) or "（无）")
            + f"\n当前持仓：{pos_txt}\n{scheme_txt}\n"
            + f"请给出最多 {max_conds} 条条件单（最长 {max_days} 天有效）。")
    return COND_RULES.format(max_conds=max_conds, max_days=max_days), user


def parse_conditions(raw: Optional[str], pool: list[str],
                     max_conds: int = 5, max_value: float = 20000.0,
                     min_ticket: float = 1000.0) -> list[dict]:
    """解析并校验 LLM 条件单：池外代码/非法条件/金额越界一律丢弃。"""
    from paper_trading.engine.conditional import CONDITION_TYPES

    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        obj = _json.loads(raw[start:end])
    except Exception:
        return []
    conds = obj.get("conditions") if isinstance(obj, dict) else None
    if not isinstance(conds, list):
        return []
    allow = set(pool or [])
    out: list[dict] = []
    for c in conds:
        if not isinstance(c, dict):
            continue
        sym = str(c.get("symbol") or "").strip()
        side = str(c.get("side") or "").strip().lower()
        cond = c.get("cond") or {}
        ctype = str(cond.get("type") or "").strip().lower()
        try:
            cval = float(cond.get("value"))
        except (TypeError, ValueError):
            continue
        try:
            vol = int(c.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        try:
            val = float(c.get("value") or 0)
        except (TypeError, ValueError):
            val = 0.0
        if len(sym) != 6 or not sym.isdigit() or sym not in allow:
            continue
        if side not in ("buy", "sell") or ctype not in CONDITION_TYPES or cval <= 0:
            continue
        if side == "buy":
            if vol and vol % 100 != 0:
                continue
            if not vol and not (min_ticket <= val <= max_value):
                continue
            if vol:
                val = 0.0
        if side == "sell" and vol % 100 != 0:
            continue
        out.append({"symbol": sym, "side": side, "cond_type": ctype,
                    "cond_value": cval, "volume": vol, "value": val,
                    "expiry": str(c.get("expiry") or "").strip(),
                    "note": str(c.get("reason") or "")[:200],
                    "source": "ai"})
        if len(out) >= max_conds:
            break
    return out