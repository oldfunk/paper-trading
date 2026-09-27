"""AI 交易员单测（fake LLM，离线可跑，覆盖钳制/幂等/坏输出）。"""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.agent import AgentConfig, AgentTrader  # noqa: E402
from paper_trading.models import Bar  # noqa: E402


def mkbar(sym, day, close):
    return Bar(symbol=sym, timestamp=datetime(2024, 1, 1) + timedelta(days=day),
               open=close, high=close + 1, low=close - 1, close=close, volume=100)


def _bridge(tmp, bars):
    import paper_trading.hermes_bridge as hb

    f1 = tempfile.mktemp(suffix=".db", dir=tmp)
    f2 = tempfile.mktemp(suffix=".db", dir=tmp)
    b = hb.HermesBridge(data_db=f1, account_db=f2, config_path="/nonexistent.yaml",
                        secrets_path=str(Path(tmp) / "s.json"))
    b.data_db.upsert_bars(bars)
    b.llm_save("custom", "https://x/v1", "m", "k")  # fake 源，chat 由单测替换
    return b


def _fresh_bars(n=30, base=10.0):
    return [mkbar("600519", 10_000 + i, base + i * 0.1) for i in range(n)]
    # day 10000+ → 日期远超今天，保证“新鲜”守卫通过


def test_agent_buy_fills_and_logs(monkeypatch, tmp_path):
    from paper_trading.llm import provider as prov

    plan = {"actions": [{"action": "buy", "symbol": "600519",
                         "volume": 100, "price": None, "reason": "测试"}],
            "summary": "买一手试试"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig(max_orders_per_run=3, max_order_value=20000.0))
    res = t.run(["600519"])
    assert res["ok"] and len(res["decisions"]) == 1
    assert res["decisions"][0]["status"].startswith("已成交")
    assert b.broker.get_position("600519").total_volume == 100
    ops = b.broker.get_op_log(5)
    assert ops[0]["action"] == "ai:decide" and ops[0]["ok"] == 1


def test_agent_rejects_bad_actions(monkeypatch, tmp_path):
    from paper_trading.llm import provider as prov

    plan = {"actions": [
        {"action": "buy", "symbol": "999999", "volume": 100, "reason": "池外"},
        {"action": "buy", "symbol": "600519", "volume": 50, "reason": "零股"},
        {"action": "dance", "symbol": "600519", "volume": 100, "reason": "乱动"},
    ], "summary": "全拒"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig())
    res = t.run(["600519"])
    assert res["ok"]
    assert all("拒绝" in d["status"] for d in res["decisions"])
    assert b.broker.get_order_history(10) == [] or all(
        o["status"] == "rejected" for o in b.broker.get_order_history(10))


def test_agent_garbage_output_safe(monkeypatch, tmp_path):
    from paper_trading.llm import provider as prov

    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": "今天天气不错！！")
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig())
    res = t.run(["600519"])
    assert res["ok"] and res["decisions"] == []


def test_agent_idempotent_same_day(monkeypatch, tmp_path):
    from paper_trading.llm import provider as prov

    plan = {"actions": [], "summary": "不动"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig())
    assert t.run(["600519"])["ok"]
    res2 = t.run(["600519"])
    assert res2.get("skipped") == "already-decided"
    # 跳过也记流水（面板可见“今日已决策”）
    assert any("already-decided" in (o["result"] or "")
               for o in b.broker.get_op_log(10) if o["action"] == "ai:decide")


def test_agent_skip_does_not_lock_day(monkeypatch, tmp_path):
    """no-fresh-bars 这类跳过不算实质决策，不锁死当日后来的真跑。"""
    from paper_trading.llm import provider as prov

    plan = {"actions": [], "summary": "不动"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig())
    assert not t._decided_today()
    b.broker.log_operation("ai:decide", {"symbols": ["600519"]}, True,
                           {"skipped": "no-fresh-bars"}, None, None)
    assert not t._decided_today()  # 跳过不锁
    assert t.run(["600519"])["ok"]  # 真跑仍可执行（fake bars 新鲜）
    assert t._decided_today()  # 实质决策后锁定


def test_agent_dry_run_changes_nothing(monkeypatch, tmp_path):
    from paper_trading.llm import provider as prov

    plan = {"actions": [{"action": "buy", "symbol": "600519",
                         "volume": 100, "price": None, "reason": "试"}], "summary": ""}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig())
    res = t.run(["600519"], dry_run=True)
    assert res["ok"] and "试运行" in res["decisions"][0]["status"]
    assert b.broker.get_position("600519") is None
