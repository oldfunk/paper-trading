"""T95 · 面板 P0：收益曲线与今日卡的口径单测（2026-11-19）。"""
import json
import math
import sqlite3
import sys
import threading
from datetime import date, datetime, timedelta
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import paper_trading.dashboard as d  # noqa: E402
from paper_trading.utils import trading_calendar as tc  # noqa: E402


def _mk_db(path, rows, *, initial=100000.0):
    """最小账本：account + nav_history（只测面板口径，不掺引擎）。"""
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE account (id INTEGER PRIMARY KEY, cash REAL,"
              " initial_cash REAL, created_at TEXT)")
    c.execute("INSERT INTO account (cash, initial_cash) VALUES (?,?)", (0.0, initial))
    c.execute("CREATE TABLE nav_history (id INTEGER PRIMARY KEY, timestamp TEXT,"
              " cash REAL, market_value REAL, total_value REAL,"
              " available_cash REAL, pnl REAL, pnl_pct REAL)")
    for ts, tv, mv in rows:
        c.execute("INSERT INTO nav_history (timestamp, cash, market_value,"
                  " total_value, available_cash, pnl, pnl_pct)"
                  " VALUES (?,?,?,?,?,?,?)",
                  (ts, 0.0, mv, tv, 0.0, tv - initial, tv / initial - 1))
    c.commit()
    c.close()


@pytest.fixture()
def cal(tmp_path, monkeypatch):
    p = tmp_path / "cal.json"
    tc.save_calendar(["2026-11-02", "2026-11-03", "2026-11-04",
                      "2026-11-05", "2026-11-06"], path=p)
    monkeypatch.setenv(tc.CALENDAR_ENV, str(p))
    tc.clear_calendar_cache()
    yield p
    tc.clear_calendar_cache()


def test_perf_metrics_known_series(tmp_path, cal):
    db = tmp_path / "a.db"
    _mk_db(db, [("2026-11-02T20:50:00", 100000.0, 100000.0),
                ("2026-11-03T20:50:00", 101000.0, 101000.0),
                ("2026-11-04T20:50:00", 100000.0, 100000.0),
                ("2026-11-05T20:50:00", 99000.0, 99000.0)])
    out = d._perf_payload(str(db))
    s = out["summary"]
    assert out["initial_cash"] == 100000.0
    assert len(out["series"]) == 4
    assert s["total_return"] == pytest.approx(-0.01, abs=1e-6)
    assert s["total_pnl"] == pytest.approx(-1000.0, abs=1e-4)
    assert s["today_pnl"] == pytest.approx(-1000.0, abs=1e-4)
    assert s["today_pnl_pct"] == pytest.approx(-0.01, abs=1e-6)
    assert s["mdd"] == pytest.approx(1 - 99000 / 101000, abs=1e-6)
    assert s["mdd_date"] == "2026-11-05"
    assert s["current_drawdown"] == pytest.approx(1 - 99000 / 101000, abs=1e-6)
    assert s["exposure"] == pytest.approx(1.0, abs=1e-6)
    assert (s["win_days"], s["lose_days"]) == (1, 2)

    rets = [0.01, 100000 / 101000 - 1, -0.01]
    mean = sum(rets) / 3
    var = sum((r - mean) ** 2 for r in rets) / 2
    sd = math.sqrt(var)
    assert s["vol_annual"] == pytest.approx(sd * math.sqrt(252), abs=1e-6)
    assert s["sharpe"] == pytest.approx(mean / sd * math.sqrt(252), abs=1e-4)


def test_perf_same_day_duplicates_take_last(tmp_path, cal):
    """reset/补记会在同一天留下两行：曲线只认最后一条，不画出两个点。"""
    db = tmp_path / "a.db"
    _mk_db(db, [("2026-11-03T10:00:00", 101000.0, 101000.0),
                ("2026-11-03T20:50:00", 100500.0, 100500.0),
                ("2026-11-04T20:50:00", 100000.0, 100000.0)])
    out = d._perf_payload(str(db))
    assert [x["date"] for x in out["series"]] == ["2026-11-03", "2026-11-04"]
    assert out["series"][0]["total_value"] == 100500.0


def test_perf_reports_missing_trading_days(tmp_path, cal):
    db = tmp_path / "a.db"
    _mk_db(db, [("2026-11-02T20:50:00", 100000.0, 100000.0),
                ("2026-11-04T20:50:00", 100000.0, 100000.0)])
    cov = d._perf_payload(str(db))["coverage"]
    assert cov["expected"] == 3 and cov["present"] == 2
    assert cov["missing"] == ["2026-11-03"]


def test_perf_single_point_has_no_risk_stats(tmp_path, cal):
    db = tmp_path / "a.db"
    _mk_db(db, [("2026-11-05T20:50:00", 97000.0, 97000.0)])
    s = d._perf_payload(str(db))["summary"]
    assert s["total_return"] == pytest.approx(-0.03)
    assert s["mdd"] is None and s["vol_annual"] is None
    assert s["win_days"] is None and s["lose_days"] is None


def test_perf_empty_db_is_not_an_error(tmp_path):
    db = tmp_path / "empty.db"
    db.write_bytes(b"")
    out = d._perf_payload(str(db))
    assert out["series"] == [] and out["summary"]["total_return"] is None
    assert out["coverage"]["present"] == 0 and out["asof"] is None


def test_today_checks_reads_last_record(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    today = date.today().isoformat()
    rec = {"kind": "daily_check", "at": today + "T15:30:03", "today": today,
           "green": 15, "warn": 3, "red": 0, "red_ids": [],
           "warn_ids": ["nav", "raw_gate"]}
    (logs / "daily_check.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    got = d._today_checks(str(tmp_path / "acct.db"), today)
    assert got["green"] == 15 and got["warn_ids"] == ["nav", "raw_gate"]
    assert d._today_checks(str(tmp_path / "acct.db"), "2020-01-01") is None


def _overview_db(path):
    today = date.today().isoformat()
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE fills (fill_id INTEGER PRIMARY KEY, symbol TEXT,"
              " direction INTEGER, volume INTEGER, price REAL, timestamp TEXT)")
    c.execute("INSERT INTO fills (symbol,direction,volume,price,timestamp)"
              " VALUES ('000001',1,100,10,?)", (today + "T09:32:00",))
    c.execute("INSERT INTO fills (symbol,direction,volume,price,timestamp)"
              " VALUES ('000002',-1,100,10,?)", (today + "T09:33:00",))
    c.execute("CREATE TABLE conditional_orders (id INTEGER PRIMARY KEY,"
              " created_at TEXT, filled_at TEXT, status TEXT)")
    for st, n, filled in (("armed", 2, ""), ("queued_rq", 1, ""),
                          ("filled", 1, today + "T09:47:05")):
        for i in range(n):
            c.execute("INSERT INTO conditional_orders (created_at, filled_at, status)"
                      " VALUES (?,?,?)", (today + "T09:00:00", filled, st))
    c.execute("CREATE TABLE rq_intents (biz_id TEXT PRIMARY KEY, created_at TEXT,"
              " status TEXT)")
    for st in ("executed", "rejected", "queued"):
        c.execute("INSERT INTO rq_intents (biz_id, created_at, status)"
                  " VALUES (?,?,?)", (st + "-1", today + "T09:30:00", st))
    c.execute("CREATE TABLE nav_history (id INTEGER PRIMARY KEY, timestamp TEXT,"
              " total_value REAL)")
    c.execute("INSERT INTO nav_history (timestamp,total_value) VALUES (?,?)",
              ((date.today() - timedelta(days=1)).isoformat() + "T20:50:00", 95000.0))
    c.execute("INSERT INTO nav_history (timestamp,total_value) VALUES (?,?)",
              (today + "T20:50:00", 96000.0))
    c.commit()
    c.close()


def test_overview_counts_and_pnl(tmp_path, monkeypatch):
    db = tmp_path / "acct.db"
    _overview_db(db)
    from paper_trading.utils import heartbeat as hb
    monkeypatch.setattr(hb, "read_status", lambda **kw: {"healthy": True,
                                                        "alerts": [],
                                                        "checked_at": "x"})
    bridge = SimpleNamespace(
        get_status=lambda: {"total_value": 97000.0, "data_asof": "2026-11-19",
                            "positions": [], "cash": 0.0, "market_value": 97000.0},
        broker=SimpleNamespace(db_path=str(db)))
    out = d._overview_payload(bridge)
    assert out["fills"] == {"n": 2, "buy": 1, "sell": 1}
    assert out["conditions"]["armed"] == 2 and out["conditions"]["queued_rq"] == 1
    assert out["conditions"]["filled_today"] == 1
    assert out["intents"]["executed_today"] == 1
    assert out["intents"]["rejected_today"] == 1
    assert out["intents"]["pending"] == 1
    # 今天已有收盘 NAV ⇒ 用 last - prev，且不是估算
    assert out["today_pnl"] == pytest.approx(1000.0)
    assert out["estimated"] is False
    assert out["health"]["healthy"] is True
    assert out.get("quote_asof") is None      # 测试 bridge 没有 data_db


def test_overview_estimates_when_today_nav_missing(tmp_path, monkeypatch):
    db = tmp_path / "acct.db"
    _overview_db(db)
    c = sqlite3.connect(str(db))
    c.execute("DELETE FROM nav_history WHERE timestamp LIKE ?",
              (date.today().isoformat() + "%",))
    c.commit()
    c.close()
    from paper_trading.utils import heartbeat as hb
    monkeypatch.setattr(hb, "read_status", lambda **kw: {"healthy": True,
                                                        "alerts": []})
    bridge = SimpleNamespace(
        get_status=lambda: {"total_value": 97000.0, "data_asof": "x",
                            "positions": [], "cash": 0.0, "market_value": 97000.0},
        broker=SimpleNamespace(db_path=str(db)))
    out = d._overview_payload(bridge)
    assert out["today_pnl"] == pytest.approx(2000.0)   # live 97000 - last 95000
    assert out["estimated"] is True


def test_positions_use_raw_prices_same_as_nav(tmp_path):
    """现价/市值/走势必须走 raw（与 NAV 同源）：qfq 日线会晚一天，混用会
    让 KPI 与曲线差出几百块（11-19 实测 96,071 vs 95,800）。"""
    db = tmp_path / "data.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE daily_bars_raw (symbol TEXT, timestamp TEXT, close REAL)")
    c.executemany("INSERT INTO daily_bars_raw VALUES (?,?,?)",
                  [("000001", "2026-11-18T00:00:00", 100.0),
                   ("000001", "2026-11-19T00:00:00", 110.0)])
    c.commit()
    c.close()
    bridge = SimpleNamespace(
        get_status=lambda: {"positions": [{
            "symbol": "000001", "total_volume": 100, "available_volume": 100,
            "avg_cost": 90.0, "current_price": 99.0, "market_value": 9900.0}],
            "cash": 1000.0, "market_value": 9900.0, "total_value": 10900.0},
        broker=SimpleNamespace(get_all_positions=lambda: []),
        data_db=SimpleNamespace(db_path=str(db)))
    rows = d._positions_payload(bridge)
    assert rows[0]["current_price"] == 110.0
    assert rows[0]["market_value"] == 11000.0
    assert rows[0]["spark"] == [100.0, 110.0]
    assert len(rows[0]["spark"]) == 2


def test_performance_endpoint(tmp_path):
    db = tmp_path / "acct.db"
    _mk_db(db, [("2026-11-18T20:50:00", 96000.0, 96000.0),
                ("2026-11-19T20:50:00", 97000.0, 97000.0)])

    class H(d.Handler):
        account_db = str(db)
        data_db = str(tmp_path / "data.db")

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        import urllib.request
        with urllib.request.urlopen(
                f"http://127.0.0.1:{srv.server_port}/api/performance",
                timeout=10) as r:
            body = json.loads(r.read().decode())
    finally:
        srv.shutdown()
        srv.server_close()
    assert body["ok"] and body["data"]["summary"]["exposure"] == pytest.approx(1.0)
    assert len(body["data"]["series"]) == 2
