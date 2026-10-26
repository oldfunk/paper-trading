"""上下文压缩：预算感知的装配后处理（P3）。

目标：母库富化后上下文变大，按 `agent.context_budget` 分级压缩，
**provenance 元数据永不被截断**（排查与 AI 分析依赖它）。

压缩顺序（先截长文本，再减条目，最后才丢低优先级块）：
1. 长文本字段截断（分析/论点/筛选理由/新闻摘要/流水）
2. 财务摘要只留关键字段；股息/追踪只留最近几条
3. 收盘价每只保留最近 N 根
4. 仍超预算：丢 intraday → recent_ops 收到 1 条 → recent_nav 收到 1 条
"""
from __future__ import annotations

import copy
import json
from typing import Optional

#: 财务摘要进上下文的字段白名单（其余丢弃，控制体积）
_FIN_KEYS = ("roe_5y_avg", "roe_10y_avg", "gross_margin_5y_avg",
             "net_margin_5y_avg", "ocf_latest", "debt_ratio_latest",
             "roic_5y_avg", "data_years")

_MAX_BARS = 10
_MAX_DIV = 5
_MAX_OUTCOME = 3


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 约 1 token/字，其余约 4 字符/token。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def context_tokens(ctx: dict) -> int:
    try:
        return estimate_tokens(json.dumps(ctx, ensure_ascii=False, default=str))
    except Exception:                       # noqa: BLE001
        return estimate_tokens(str(ctx))


def truncate(text, max_chars: int, suffix: str = "…[截断]") -> str:
    s = str(text or "")
    if max_chars <= 0 or len(s) <= max_chars:
        return s
    return s[:max_chars] + suffix


def _truncate_cards(cards: dict) -> None:
    for card in (cards or {}).values():
        if not isinstance(card, dict):
            continue
        ai = card.get("ai_analysis")
        if isinstance(ai, dict):
            ai["analysis"] = truncate(ai.get("analysis"), 400)
            ai["trade_strategy"] = truncate(ai.get("trade_strategy"), 200)
        th = card.get("thesis")
        if isinstance(th, dict):
            th["core"] = truncate(th.get("core"), 150)
            th["sell_conditions"] = truncate(th.get("sell_conditions"), 150)
        fin = card.get("financial_summary")
        if isinstance(fin, dict):
            card["financial_summary"] = {k: fin.get(k) for k in _FIN_KEYS
                                         if k in fin}
        if isinstance(card.get("dividend"), list):
            card["dividend"] = card["dividend"][-_MAX_DIV:]
        if isinstance(card.get("outcomes"), list):
            card["outcomes"] = card["outcomes"][-_MAX_OUTCOME:]
        notes = card.get("notes")
        if isinstance(notes, list):
            for n in notes:
                if isinstance(n, dict):
                    n["note"] = truncate(n.get("note"), 120)


def _truncate_screening(scr) -> None:
    if not isinstance(scr, dict):
        return
    for r in (scr.get("rows") or []):
        if isinstance(r, dict):
            r["reason"] = truncate(r.get("reason"), 200)
            r["score_detail"] = truncate(r.get("score_detail"), 200)


def compress_context(ctx: dict, budget: int = 8000) -> dict:
    """超预算时分级压缩；不修改入参，返回新 dict。provenance 保留。"""
    if not isinstance(ctx, dict) or budget <= 0:
        return ctx
    if context_tokens(ctx) <= budget:
        return ctx
    out = copy.deepcopy(ctx)
    # 1. 长文本截断
    ops = out.get("recent_ops")
    if isinstance(ops, list):
        for o in ops:
            if isinstance(o, dict):
                o["params"] = truncate(o.get("params"), 100)
                o["result"] = truncate(o.get("result"), 120)
    vv = out.get("value_view")
    if isinstance(vv, dict):
        _truncate_cards(vv.get("stock_cards") or {})
        _truncate_screening(vv.get("screening"))
    news = out.get("news")
    if isinstance(news, dict) and isinstance(news.get("items"), list):
        for it in news["items"]:
            if isinstance(it, dict):
                it["summary"] = truncate(it.get("summary"), 120)
                it["ai_brief"] = truncate(it.get("ai_brief"), 120)
    # 2. 收盘价每只留最近 N 根
    closes = out.get("recent_closes")
    if isinstance(closes, dict):
        out["recent_closes"] = {k: (v[-_MAX_BARS:] if isinstance(v, list) else v)
                                for k, v in closes.items()}
    # 3. 仍超预算：丢低优先级块
    if context_tokens(out) > budget and isinstance(out.get("intraday"), dict):
        out["intraday"] = {"note": "盘中快照已按预算省略"}
    if context_tokens(out) > budget and isinstance(out.get("recent_ops"), list):
        out["recent_ops"] = out["recent_ops"][:1]
    if context_tokens(out) > budget and isinstance(out.get("recent_nav"), list):
        out["recent_nav"] = out["recent_nav"][:1]
    return out
