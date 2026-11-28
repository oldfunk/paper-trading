"""T129：前复权锚点/源切换检测（M1.5）。"""
import sqlite3
from datetime import datetime, timedelta

from paper_trading.data.db_manager import DataDBManager
from paper_trading.data.qfq_check import check
from paper_trading.integration import mother as m
from paper_trading.models import Bar


def _bars(n=20, base=10.0, jump_day=None, jump=1.0):
    out = []
    for i in range(n):
        px = base + i * 0.1
        if jump_day is not None and i >= jump_day:
            px *= jump
        out.append(Bar(symbol="600519",
                       timestamp=datetime(2026, 10, 1) + timedelta(days=i),
                       open=px, high=px, low=px, close=px, volume=1000,
                       adjust="qfq"))
    return out


def _mother(tmp_path, bars, *, adj="qfq"):
    sd = tmp_path / "sd"
    (sd / "data" / "db").mkdir(parents=True)
    p = sd / "data" / "db" / "stock_dashboard.db"
    c = sqlite3.connect(p)
    c.execute("""CREATE TABLE kline_daily (
        code TEXT, trade_date TEXT, open REAL, high REAL, low REAL,
        close REAL, volume REAL, amount REAL, close_adj REAL, close_qfq REAL)""")
    for b in bars:
        d = b.timestamp.date().isoformat()
        cq = b.close if adj == "qfq" else None
        ca = b.close * 1.05 if adj == "hfq" else None
        c.execute("INSERT INTO kline_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
                  ("600519", d, b.open, b.high, b.low, b.close, 1000, 1e6,
                   ca, cq))
    c.commit()
    c.close()
    m.reset_cache()
    return sd


def _local(tmp_path, bars):
    db = DataDBManager(tmp_path / "data.db")
    db.upsert_bars(bars)
    return db


def test_qfq_ratio_stable(tmp_path):
    mbars = _bars()
    sd = _mother(tmp_path, mbars)
    db = _local(tmp_path, _bars())
    try:
        out = check(db, ["600519"], days=30, mother=sd)
        assert out["status"] == "green" and out["ok"] is True
        assert out["items"][0]["status"] == "ok"
        assert out["items"][0]["common_days"] >= 10
    finally:
        m.reset_cache()


def test_qfq_anchor_shift_detected(tmp_path):
    mbars = _bars()
    sd = _mother(tmp_path, mbars)
    # 本地在后半段整体 ×1.1：模拟本地刚重锚、母库没有
    db = _local(tmp_path, _bars(jump_day=10, jump=1.1))
    try:
        out = check(db, ["600519"], days=30, mother=sd, tol=0.001)
        assert out["status"] == "red" and out["ok"] is False
        item = out["items"][0]
        assert item["status"] == "anchor_shift"
        assert item["max_dev"] > 0.05
    finally:
        m.reset_cache()


def test_qfq_scale_mismatch_detected(tmp_path):
    mbars = _bars()
    sd = _mother(tmp_path, mbars)
    db = _local(tmp_path, _bars())          # 本地 10，母库 ×2 -> 比值 0.5
    # 把母库改成两倍（直接用另一套 bars）
    sd2 = _mother(tmp_path / "x", [Bar(
        symbol="600519", timestamp=b.timestamp, open=b.open * 2,
        high=b.high * 2, low=b.low * 2, close=b.close * 2, volume=1000,
        adjust="qfq") for b in mbars])
    try:
        out = check(db, ["600519"], days=30, mother=sd2)
        assert out["items"][0]["status"] == "scale_mismatch"
        assert out["status"] == "red"
    finally:
        m.reset_cache()


def test_qfq_hfq_is_uncomparable(tmp_path):
    mbars = _bars()
    sd = _mother(tmp_path, mbars, adj="hfq")
    db = _local(tmp_path, _bars())
    try:
        out = check(db, ["600519"], days=30, mother=sd)
        assert out["items"][0]["status"] == "uncomparable"
        assert out["status"] == "warn"
    finally:
        m.reset_cache()


def test_qfq_empty_pool_skips(tmp_path):
    db = DataDBManager(tmp_path / "data.db")
    out = check(db, [], days=30, mother=tmp_path / "nope")
    assert out["status"] == "skip" and out["ok"] is True
