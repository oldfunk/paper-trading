"""上下文压缩：预算感知、provenance 不被截断。"""
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.agent.context import (compress_context, context_tokens,  # noqa: E402
                                         estimate_tokens, truncate)


def _big_ctx():
    return {
        "account": {"cash": 1},
        "data_provenance": {"regime": {"source": "mother:market_index",
                                       "asof": "2026-01-01", "stale": True}},
        "recent_closes": {"600519": [{"date": f"2026-01-{i:02d}", "close": 1.0}
                                     for i in range(1, 31)]},
        "value_view": {"stock_cards": {"600519": {
            "ai_analysis": {"analysis": "长" * 2000, "trade_strategy": "短" * 500},
            "thesis": {"core": "论" * 1000, "sell_conditions": "卖" * 1000},
            "financial_summary": {"roe_5y_avg": 20, "roe_10y_avg": 18,
                                  "junk": "x" * 500},
            "dividend": [{"report_date": f"202{i}"} for i in range(10)],
            "outcomes": [{"analysis_date": f"202{i}"} for i in range(10)],
        }}, "screening": {"rows": [{"reason": "理" * 1000,
                                    "score_detail": "d" * 1000}]}},
        "news": {"items": [{"summary": "新" * 1000, "ai_brief": "快" * 1000}]},
        "recent_ops": [{"params": "p" * 1000, "result": "r" * 1000}
                       for _ in range(20)],
        "intraday": {"quotes": {"600519": {"price": 1}}},
    }


def test_estimate_tokens_and_truncate():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("中文") == 2
    assert truncate("x" * 100, 10).endswith("…[截断]")


def test_compress_over_budget_and_keep_provenance():
    ctx = _big_ctx()
    before = context_tokens(ctx)
    out = compress_context(ctx, budget=500)
    assert context_tokens(out) < before
    assert out["data_provenance"] == ctx["data_provenance"]
    card = out["value_view"]["stock_cards"]["600519"]
    assert "junk" not in card["financial_summary"]
    assert len(card["dividend"]) == 5 and len(card["outcomes"]) == 3
    assert len(out["recent_closes"]["600519"]) == 10
    assert out["news"]["items"][0]["summary"].endswith("…[截断]")


def test_compress_under_budget_is_noop():
    ctx = {"a": 1, "data_provenance": {"x": 1}}
    assert compress_context(ctx, budget=10000) is ctx


def test_llm_context_uses_budget(tmp_path):
    import paper_trading.cli as hb

    b = hb.TradingBridge(data_db=tempfile.mktemp(suffix=".db", dir=tmp_path),
                         account_db=tempfile.mktemp(suffix=".db", dir=tmp_path),
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp_path / "s.json"))
    b.agent_cfg = types.SimpleNamespace(context_budget=1, news_enabled=False)
    ctx = b.llm_context(max_ops=1, max_nav=1, closes_n=1, symbols=[])
    assert isinstance(ctx, dict) and "account" in ctx
