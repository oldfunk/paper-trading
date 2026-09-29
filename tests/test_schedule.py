"""AI 定时单测（纯函数 + 本地文件往返，全离线；monkepatch 时间保证确定性）。"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.agent import schedule as sch  # noqa: E402


def test_parse_slots_ok():
    assert sch.parse_slots("16:45") == ["16:45"]
    assert sch.parse_slots("17:30,16:45") == ["16:45", "17:30"]
    assert sch.parse_slots(" 16:45 , 16:45,,") == ["16:45"]
    assert sch.parse_slots(["9:5"]) == ["09:05"]


def test_parse_slots_bad():
    for bad in ["", "   ", "25:00", "16:60", "noon", "16-45", 123]:
        with pytest.raises(ValueError):
            sch.parse_slots(bad)
    with pytest.raises(ValueError):
        sch.parse_slots(",".join(f"{10+i:02d}:00" for i in range(6)))


def test_parse_max_runs():
    assert sch.parse_max_runs(1) == 1
    assert sch.parse_max_runs("3") == 3
    for bad in [0, 6, -1, "x", None, 2.5]:
        with pytest.raises(ValueError):
            sch.parse_max_runs(bad)


def test_save_load_roundtrip(tmp_path):
    ok, msg = sch.save_schedule(tmp_path, "16:45,17:30", 2)
    assert ok, msg
    cur = sch.load_schedule(tmp_path)
    assert cur == {"slots": ["16:45", "17:30"], "max_runs": 2, "source": "local"}


def test_load_defaults_when_absent(tmp_path):
    cur = sch.load_schedule(tmp_path)
    assert cur["slots"] == ["16:45"] and cur["max_runs"] == 1
    assert cur["source"] == "default"


def test_save_rejects_bad_and_keeps_old(tmp_path):
    assert sch.save_schedule(tmp_path, "16:45", 1)[0]
    assert not sch.save_schedule(tmp_path, "99:99", 1)[0]
    assert not sch.save_schedule(tmp_path, "16:45", 9)[0]
    assert sch.load_schedule(tmp_path)["slots"] == ["16:45"]


def test_due_and_consumed():
    at = lambda hm: datetime(2026, 9, 29, int(hm[:2]), int(hm[3:]))
    assert sch.due_slots(["16:45", "17:30"], at("16:00")) == []
    assert sch.due_slots(["16:45", "17:30"], at("17:00")) == ["16:45"]
    # 16:50 的一次实盘消费 16:45 时段；17:35 的消费 17:30
    assert sch.consumed_slots(["16:45", "17:30"], ["16:50"]) == {"16:45"}
    assert sch.consumed_slots(["16:45", "17:30"], ["16:50", "17:35"]) == {"16:45", "17:30"}
    # 时段前的决策不消费任何时段
    assert sch.consumed_slots(["16:45"], ["09:00"]) == set()


def test_check_gate():
    at = lambda hm: datetime(2026, 9, 29, int(hm[:2]), int(hm[3:]))
    ok, reason, slot = sch.check(["16:45"], 1, [], at("16:00"))
    assert (ok, reason) == (False, "not-in-schedule")
    ok, reason, slot = sch.check(["16:45"], 1, [], at("17:00"))
    assert (ok, reason, slot) == (True, "ok", "16:45")
    ok, reason, _ = sch.check(["16:45"], 1, ["16:50"], at("18:00"))
    assert (ok, reason) == (False, "max-runs-reached")
    # 两次机会：第一时段消费完，第二时段到点仍放行
    ok, reason, slot = sch.check(["16:45", "17:30"], 2, ["16:50"], at("17:35"))
    assert (ok, reason, slot) == (True, "ok", "17:30")
    # 两次机会但时段只有一个：消费完即无可跑时段
    ok, reason, _ = sch.check(["16:45"], 2, ["16:50"], at("18:00"))
    assert (ok, reason) == (False, "not-in-schedule")


def _bridge(tmp, bars):
    import tempfile

    import paper_trading.cli as hb

    f1 = tempfile.mktemp(suffix=".db", dir=tmp)
    f2 = tempfile.mktemp(suffix=".db", dir=tmp)
    b = hb.TradingBridge(data_db=f1, account_db=f2, config_path="/nonexistent.yaml",
                         secrets_path=str(Path(tmp) / "s.json"))
    b.data_db.upsert_bars(bars)
    b.llm_save("custom", "https://x/v1", "m", "k")  # fake 源，chat 由单测替换
    return b


def _fresh():
    from datetime import timedelta

    from paper_trading.models import Bar
    return [Bar(symbol="600519",
                timestamp=datetime(2026, 9, 29) + timedelta(days=10000 + i),
                open=10.0, high=11.0, low=9.0, close=10.0, volume=100)
            for i in range(30)]


def test_run_skips_before_slot(monkeypatch, tmp_path):
    """时段未到：实盘 run 直接跳过，不碰 LLM。"""
    from paper_trading.agent import AgentTrader
    from paper_trading.cli import TradingBridge
    from paper_trading.llm import provider as prov

    monkeypatch.setattr(TradingBridge, "sync_data",
                        lambda self, symbols: {"ok": True, "symbols": symbols,
                                               "updated": {}})
    monkeypatch.setattr(sch, "_now", lambda: datetime(2026, 9, 29, 9, 0))
    monkeypatch.setattr(prov, "chat", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("时段外不应问 LLM")))
    b = _bridge(str(tmp_path), _fresh())
    res = AgentTrader(b).run(["600519"], sched_root=str(tmp_path))
    assert res.get("skipped") == "not-in-schedule"


def test_run_second_slot_after_first(monkeypatch, tmp_path):
    """两次机会：首跑消费 16:45，次跑在 17:30 放行并记入流水。"""
    import json

    from paper_trading.agent import AgentTrader
    from paper_trading.cli import TradingBridge
    from paper_trading.llm import provider as prov

    monkeypatch.setattr(TradingBridge, "sync_data",
                        lambda self, symbols: {"ok": True, "symbols": symbols,
                                               "updated": {}})
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(
        {"actions": [], "summary": "不动"}))
    assert sch.save_schedule(tmp_path, "16:45,17:30", 2)[0]
    b = _bridge(str(tmp_path), _fresh())
    t = AgentTrader(b)
    monkeypatch.setattr(sch, "_now", lambda: datetime(2026, 9, 29, 16, 50))
    assert t.run(["600519"], sched_root=str(tmp_path))["ok"]
    monkeypatch.setattr(sch, "_now", lambda: datetime(2026, 9, 29, 17, 35))
    r2 = t.run(["600519"], sched_root=str(tmp_path))
    assert r2["ok"] and "skipped" not in r2
    assert len(t._live_runs_today()) == 2
    monkeypatch.setattr(sch, "_now", lambda: datetime(2026, 9, 29, 17, 40))
    assert t.run(["600519"], sched_root=str(tmp_path))["skipped"] == "max-runs-reached"


def test_cli_schedule_list_set(tmp_path, monkeypatch):
    """CLI 定时管理：list 读默认/set 落盘/非法输入拒绝（cwd 隔离防污染仓库）。"""
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    import os as _os
    env = dict(_os.environ, PYTHONPATH=str(ROOT))
    base = [sys.executable, "-m", "paper_trading.cli", "agent", "schedule"]

    def _run(*a):
        r = subprocess.run(base + list(a), capture_output=True, text=True,
                           cwd=str(tmp_path), env=env)
        return r, json.loads(r.stdout[r.stdout.index("{"):])

    r, d = _run("list", "--json")
    assert r.returncode == 0, r.stderr
    assert d["data"]["slots"] == ["16:45"]
    r, _ = _run("set", "--slots", "16:45,17:30", "--max-runs", "2")
    assert r.returncode == 0, r.stderr
    r, d = _run("list", "--json")
    assert d["data"]["slots"] == ["16:45", "17:30"] and d["data"]["max_runs"] == 2
    r = subprocess.run(base + ["set", "--slots", "99:99"], capture_output=True,
                       text=True, cwd=str(tmp_path), env=env)
    assert r.returncode != 0
