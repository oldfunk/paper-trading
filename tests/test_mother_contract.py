"""母库富化契约：逐接口降级 + 来源标注（合成母库，离线可跑；真母库存在时另做列校验）。"""
import sqlite3
import sys
from pathlib import Path

from datetime import date

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.integration import mother  # noqa: E402
from paper_trading.integration.settings import mother_db_path, mother_dir  # noqa: E402
from paper_trading.integration.sources import (SRC_LOCAL_AKSHARE,  # noqa: E402
                                               SRC_UNAVAILABLE)

REQUIRED = {
    "kline_daily": {"code", "trade_date", "close"},
    "financial_summary": {"stock_code"},
    "dividend_annual": {"stock_code"},
    "benchmark_daily": {"code", "trade_date", "close"},
    "news_item": {"code", "title"},
    "screening_result": {"run_id", "run_date"},
    "watchlist_thesis": {"code"},
    "analysis_outcome": {"stock_code"},
    "market_index": {"index_code", "date"},
    "instrument": {"symbol", "instrument_id"},
    "trading_calendar": {"market_id", "trade_date", "is_open"},
}


def _mk_sd(root: Path) -> Path:
    """造一个最小可用的母库目录（含 src/ 与 data/db/stock_dashboard.db）。"""
    sd = root / "stock-dashboard"
    (sd / "src").mkdir(parents=True, exist_ok=True)
    dbdir = sd / "data" / "db"
    dbdir.mkdir(parents=True, exist_ok=True)
    db = dbdir / "stock_dashboard.db"
    c = sqlite3.connect(db)
    c.executescript("""
    CREATE TABLE kline_daily (code TEXT, trade_date TEXT, open REAL, high REAL,
        low REAL, close REAL, volume REAL, amount REAL, close_raw REAL,
        close_adj REAL, turnover REAL, shares REAL);
    CREATE TABLE financial_summary (stock_code TEXT PRIMARY KEY, roe_5y_avg REAL,
        roe_10y_avg REAL, updated_at TEXT);
    CREATE TABLE dividend_annual (stock_code TEXT, report_date TEXT,
        cash_per_share REAL, eps REAL, reported_yield REAL, source TEXT);
    CREATE TABLE benchmark_daily (code TEXT, trade_date TEXT, close REAL);
    CREATE TABLE news_item (id INTEGER PRIMARY KEY, code TEXT, name TEXT, title TEXT,
        summary TEXT, url TEXT, source TEXT, published_at TEXT, fetched_at TEXT,
        sentiment REAL, ai_brief TEXT, summarized_at TEXT);
    CREATE TABLE screening_result (id INTEGER PRIMARY KEY, run_id TEXT, run_date TEXT,
        code TEXT, name TEXT, score REAL, pe REAL, pb REAL, roe REAL, reason TEXT,
        strategy_tags TEXT, dividend_yield REAL, score_detail TEXT, status TEXT);
    CREATE TABLE watchlist_thesis (id INTEGER PRIMARY KEY, code TEXT UNIQUE,
        core_thesis TEXT, assumptions TEXT, red_lines TEXT, sell_conditions TEXT);
    CREATE TABLE analysis_outcome (analysis_id INTEGER PRIMARY KEY, stock_code TEXT,
        analysis_date TEXT, signal TEXT, confidence REAL, verdict TEXT,
        entry_price REAL, ret_20 REAL, ret_60 REAL, excess_20 REAL, excess_60 REAL);
    CREATE TABLE market_index (id INTEGER PRIMARY KEY, index_code TEXT,
        index_name TEXT, current_value REAL, change_percent REAL, pe REAL, pb REAL,
        date TEXT);
    CREATE TABLE instrument (instrument_id TEXT, market_id TEXT, symbol TEXT,
        exchange TEXT, name TEXT, status TEXT);
    CREATE TABLE trading_calendar (market_id TEXT, trade_date TEXT, is_open INTEGER,
        session_json TEXT, source TEXT);
    """)
    c.execute("""INSERT INTO kline_daily VALUES
        ('600519','2026-10-26',1500,1510,1490,1505,1000000,1500000000,1505,1505,0.1,1000000000),
        ('600519','2026-10-23',1490,1500,1480,1495,900000,1300000000,1495,1495,0.1,1000000000)""")
    c.execute("INSERT INTO financial_summary VALUES ('600519', 20.5, 18.2, '2026-10-23T22:00:00')")
    c.execute("INSERT INTO dividend_annual VALUES ('600519','2025-12-31', 3.0, 6.5, 2.0, 'em')")
    c.execute("INSERT INTO benchmark_daily VALUES ('000001','2026-10-26', 11000)")
    c.execute("""INSERT INTO news_item (code,name,title,summary,source,published_at,sentiment,ai_brief)
        VALUES ('600519','贵州茅台','测试标题','测试摘要','akshare_em','2026-10-26 09:00',0.2,'测试简报')""")
    c.execute("""INSERT INTO screening_result (run_id,run_date,code,name,score,pe,pb,roe,reason,strategy_tags,dividend_yield,score_detail,status)
        VALUES ('r1','2026-10-23','600519','贵州茅台',90.0,20.0,5.0,20.0,'测试理由','value',2.0,'{"total":90}','active')""")
    c.execute("INSERT INTO watchlist_thesis (code,core_thesis,sell_conditions) VALUES ('600519','测试论点','跌破 1400 卖出')")
    c.execute("""INSERT INTO analysis_outcome (stock_code,analysis_date,signal,confidence,verdict,entry_price)
        VALUES ('600519','2026-10-23','buy',0.8,'通过','1495')""")
    c.execute("INSERT INTO market_index (index_code,index_name,current_value,change_percent,pe,pb,date) VALUES ('000001','上证指数',3300,0.5,13.0,1.3,'2026-10-26')")
    c.execute("INSERT INTO instrument VALUES ('CN:600519','CN','600519','SSE','贵州茅台','active')")
    c.execute("INSERT INTO trading_calendar VALUES ('CN','2026-10-27',1,'{}','test')")
    c.execute("INSERT INTO trading_calendar VALUES ('CN','2026-10-26',1,'{}','test')")
    c.commit()
    c.close()
    return sd


@pytest.fixture()
def sd(tmp_path):
    mother.reset_cache()
    yield _mk_sd(tmp_path)
    mother.reset_cache()


def _local_bar(code, days):
    return [{"timestamp": f"2026-10-2{i}T00:00:00", "open": 10, "high": 11,
             "low": 9, "close": 10 + i, "volume": 1000}
            for i in range(1, 4)]


def test_block_marks_stale_by_asof():
    from paper_trading.integration.sources import block

    old = block({"x": 1}, "mother:t", asof="2020-01-01", max_age_days=7)
    assert old["stale"] is True
    fresh = block({"x": 1}, "mother:t",
                  asof=date.today().isoformat(), max_age_days=7)
    assert fresh["stale"] is False


def test_daily_bars_mother_first_then_local(sd):
    out = mother.daily_bars(["600519", "000001"], days=5,
                            local_bars=_local_bar, mother=sd)
    assert out["600519"]["source"] == "mother:kline_daily"
    assert out["600519"]["available"] and out["600519"]["asof"] == "2026-10-26"
    assert out["600519"]["bars"][0]["close"] == 1505
    assert out["600519"]["stale"] is False
    assert out["000001"]["source"] == SRC_LOCAL_AKSHARE
    assert out["000001"]["available"]


def test_daily_bars_unavailable_without_mother(tmp_path):
    out = mother.daily_bars(["600519"], local_bars=None, mother=tmp_path)
    assert out["600519"]["source"] == SRC_UNAVAILABLE
    assert not out["600519"]["available"] and out["600519"]["bars"] == []


def test_financial_summary_and_unavailable(sd):
    out = mother.financial_summary(["600519", "000001"], mother=sd)
    assert out["600519"]["source"] == "mother:financial_summary"
    assert out["600519"]["data"]["roe_5y_avg"] == 20.5
    assert out["000001"]["source"] == SRC_UNAVAILABLE


def test_dividend_benchmark_regime(sd):
    d = mother.dividend(["600519"], mother=sd)
    assert d["600519"]["source"] == "mother:dividend_annual"
    b = mother.benchmark(["000001"], mother=sd)
    assert b["000001"]["source"] == "mother:benchmark_daily"
    r = mother.regime(mother=sd)
    assert r["source"] == "mother:market_index" and r["data"][0]["index_code"] == "000001"


def test_news_mother_preferred_then_local(sd):
    out = mother.news(codes=["600519"], mother=sd)
    assert out["source"] == "mother:news_item"
    assert out["data"][0]["title"] == "测试标题"
    # 母库无该表时回退本地
    empty = sd.parent / "empty-sd"
    (empty / "src").mkdir(parents=True)
    (empty / "data" / "db").mkdir(parents=True)
    sqlite3.connect(empty / "data" / "db" / "stock_dashboard.db").close()
    mother.reset_cache()
    local = {"available": True, "items": [{"title": "本地快讯"}],
             "fetched_at": "2026-10-26"}
    out2 = mother.news(codes=["600519"], local_news=lambda: local, mother=empty)
    assert out2["source"] == SRC_LOCAL_AKSHARE


def test_screening_detail_and_local_pool(sd):
    out = mother.screening_detail(mother=sd)
    assert out["source"] == "mother:screening_result"
    assert out["data"]["rows"][0]["score_detail"] == '{"total":90}'
    out2 = mother.screening_detail(local_pool=["600519"],
                                  mother=sd.parent / "no-such-sd")
    assert out2["source"] == "local:config"


def test_thesis_and_outcomes(sd):
    t = mother.thesis(["600519"], mother=sd)
    assert t["600519"]["source"] == "mother:watchlist_thesis"
    o = mother.outcomes(["600519"], mother=sd)
    assert o["600519"]["source"] == "mother:analysis_outcome"


def test_instrument_map_and_trading_calendar(sd):
    im = mother.instrument_map(["600519", "000001"], mother=sd)
    assert im["600519"]["source"] == "mother:instrument"
    assert im["600519"]["data"]["instrument_id"] == "CN:600519"
    assert im["000001"]["source"] == SRC_UNAVAILABLE

    tc = mother.trading_calendar(mother=sd)
    assert tc["source"] == "mother:trading_calendar"
    assert any(r["is_open"] == 1 for r in tc["data"])


def test_compare_bars_local_vs_mother(sd):
    out = mother.compare_bars(
        ["600519"], days=10, mother=sd,
        local_bars=lambda c, d: [{"timestamp": f"2026-10-2{i}T00:00:00",
                                  "open": 10, "high": 11, "low": 9,
                                  "close": 10 + i, "volume": 1}
                                 for i in range(1, 4)])
    r = out["600519"]
    assert r["local"]["adj"] == "qfq"
    assert r["mother"]["adj"] in ("hfq", "raw")
    assert r["cross_diff"] == {"golden": 0, "death": 0}
    assert "return_corr" in r


def test_coverage_summary(sd):
    cov = mother.coverage(["600519"], local_bars=lambda c, d: [], mother=sd)
    for k in ("daily_bars", "financial_summary", "instrument", "screening",
              "regime", "trading_calendar"):
        assert k in cov
    assert cov["instrument"]["600519"]["source"] == "mother:instrument"


def test_real_mother_contract_if_present():
    """真母库契约校验：显式指定 ``PAPER_TRADING_MOTHER_DIR``（推荐）。

    单测默认隔离母库（见 ``conftest._isolated_mother``）；要在 Pi 上验证真库：

    ```bash
    PAPER_TRADING_MOTHER_DIR=/home/pi/stock-dashboard \\
      venv/bin/python -m pytest tests/test_mother_contract.py -q
    ```
    """
    import os

    explicit = os.environ.get("PAPER_TRADING_MOTHER_DIR")
    if not explicit:
        pytest.skip("未指定 PAPER_TRADING_MOTHER_DIR")
    sd = mother_dir(explicit)
    db = mother_db_path(sd) if sd is not None else None
    if db is None:
        pytest.skip(f"指定目录不是可用母库：{explicit}")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for table, cols in REQUIRED.items():
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert have, f"母库缺表 {table}"
            missing = cols - have
            assert not missing, f"母库表 {table} 缺列 {missing}"
    finally:
        conn.close()
