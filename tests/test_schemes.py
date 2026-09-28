"""投资方案单测（离线；母项目用临时目录模拟缺失/存在）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.strategy.schemes import (  # noqa: E402
    active_name,
    all_schemes,
    mother_strategies,
    resolve_scheme,
    set_active,
)


def test_builtin_and_unknown_fallback(tmp_path):
    ss = all_schemes(str(tmp_path))
    assert set(["ma_trend", "value_follow", "defense"]) <= set(ss)
    assert resolve_scheme("nope", str(tmp_path)).name == "ma_trend"  # 未知名回退
    assert resolve_scheme("defense", str(tmp_path)).allow_buy is False
    name, src = active_name(str(tmp_path), "ma_trend")
    assert (name, src) == ("ma_trend", "default")


def test_local_override_and_set_active(tmp_path):
    (tmp_path / "strategy.local.yaml").write_text(
        "active: defense\ncustom:\n  mine:\n    title: 自选\n"
        "    universe: {source: config}\n",
        encoding="utf-8")
    name, src = active_name(str(tmp_path), "ma_trend")
    assert (name, src) == ("defense", "local")
    assert all_schemes(str(tmp_path))["mine"].title == "自选"
    ok, msg = set_active(str(tmp_path), "mine")
    assert ok and "mine" in msg
    assert active_name(str(tmp_path), "ma_trend")[0] == "mine"
    ok, msg = set_active(str(tmp_path), "ghost")
    assert not ok  # 未知方案拒绝写入


def test_mother_missing_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(tmp_path / "nothing"))
    assert mother_strategies() == []


def test_mother_strategies_listed(tmp_path, monkeypatch):
    mdir = tmp_path / "sd"
    (mdir / "config").mkdir(parents=True)
    (mdir / "src").mkdir()
    (mdir / "config" / "strategies.yaml").write_text(
        "growth:\n  name: 成长型\n  desc: d\n  pe_max: 50\n",
        encoding="utf-8")
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(mdir))
    ms = mother_strategies()
    assert ms and ms[0]["key"] == "growth" and ms[0]["n_rules"] == 1


def test_defense_blocks_buys(monkeypatch, tmp_path):
    """defense 方案下买入被硬拦截（卖出放行）。"""
    import json
    import tempfile

    import paper_trading.hermes_bridge as hb
    from paper_trading.agent import AgentTrader
    from paper_trading.llm import provider as prov
    from paper_trading.models import Bar
    from datetime import datetime, timedelta
    import tempfile

    plan = {"actions": [
        {"action": "buy", "symbol": "600519", "volume": 100, "reason": "买"},
        {"action": "sell", "symbol": "600519", "volume": 100, "reason": "卖"},
    ], "summary": "攻守"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    f1 = tempfile.mktemp(suffix=".db", dir=str(tmp_path))
    f2 = tempfile.mktemp(suffix=".db", dir=str(tmp_path))
    b = hb.HermesBridge(data_db=f1, account_db=f2, config_path="/nonexistent.yaml",
                        secrets_path=str(tmp_path / "s.json"), scheme="defense")
    assert b.scheme.name == "defense" and b.scheme_source == "cli"
    bars = [Bar(symbol="600519", timestamp=datetime(2024, 1, 1) + timedelta(days=10000 + i),
                open=10, high=11, low=9, close=10, volume=100) for i in range(30)]
    b.data_db.upsert_bars(bars)
    b.llm_save("custom", "https://x/v1", "m", "k")
    res = AgentTrader(b, b.agent_cfg).run(["600519"], dry_run=True)
    by_action = {d["action"]: d["status"] for d in res["decisions"]}
    assert "禁止买入" in by_action["buy"]
    assert "禁止买入" not in by_action["sell"]  # 卖出通道不受方案买入禁令影响
