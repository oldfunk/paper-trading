"""Phase1-4 回归单测（离线，不依赖 akshare/网络）。"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.broker.paper_broker import PaperBroker
from paper_trading.data.db_manager import DataDBManager
from paper_trading.models import Bar, Order, OrderType, Signal, SignalType, TradingConfig
from paper_trading.risk.risk_manager import RiskManager
from paper_trading.strategy.ma_cross_strategy import MACrossStrategy
from paper_trading.utils.trading_calendar import is_trading_day, next_trading_day


def mkbar(sym, day, close):
    return Bar(symbol=sym, timestamp=datetime(2024, 1, 1) + timedelta(days=day),
               open=close, high=close + 1, low=close - 1, close=close, volume=100)


def test_get_bars_returns_latest():
    f = tempfile.mktemp(suffix=".db")
    db = DataDBManager(f)
    bars = [mkbar("600519", i, 10 + i) for i in range(50)]
    db.upsert_bars(bars)
    got = db.get_bars("600519", limit=30)
    assert len(got) == 30
    assert got[0].close == 30.0 and got[-1].close == 59.0
    Path(f).unlink(missing_ok=True)


def test_signal_only_on_last_cross():
    s = MACrossStrategy(5, 20)
    # 构造最后一根金叉
    closes = [10] * 20 + [9, 9, 9, 9, 20]
    bl = [mkbar("X", i, c) for i, c in enumerate(closes)]
    sigs = s.generate_signals({"X": bl})
    assert "X" in sigs and sigs["X"].direction == SignalType.BUY
    # 再加一根无交叉 → 应无信号（去重）
    bl2 = bl + [mkbar("X", len(bl), 20)]
    assert s.generate_signals({"X": bl2}) == {}


def test_t1_calendar():
    assert not is_trading_day(datetime(2026, 9, 19))  # 周六
    assert next_trading_day(datetime(2026, 9, 18)).isoformat() == "2026-09-21"  # 周五→周一
    f = tempfile.mktemp(suffix=".db")
    b = PaperBroker(f, TradingConfig(initial_cash=100000))
    b.submit_order(Order(symbol="600519", direction=1, volume=100,
                         order_type=OrderType.LIMIT, limit_price=10.0))
    assert b.get_position("600519").available_volume == 0
    n = b.unfreeze_t1(date=datetime(2026, 9, 22))
    assert n == 1 and b.get_position("600519").available_volume == 100
    Path(f).unlink(missing_ok=True)


def test_rejected_persisted_and_volume_rule():
    f = tempfile.mktemp(suffix=".db")
    b = PaperBroker(f, TradingConfig(initial_cash=100000))
    r = b.submit_order(Order(symbol="600519", direction=1, volume=50,
                             order_type=OrderType.LIMIT, limit_price=10.0))
    assert r.status.value == "rejected"
    hist = b.get_order_history(5)
    assert hist[0]["status"] == "rejected"
    Path(f).unlink(missing_ok=True)


def test_limit_band_and_avg_cost_with_fees():
    f = tempfile.mktemp(suffix=".db")
    b = PaperBroker(f, TradingConfig(initial_cash=1000000))
    r = b.submit_order(Order(symbol="600519", direction=1, volume=100,
                             order_type=OrderType.LIMIT, limit_price=100.0, prev_close=80.0))
    assert r.status.value == "rejected"
    b.submit_order(Order(symbol="600519", direction=1, volume=100,
                         order_type=OrderType.LIMIT, limit_price=10.0))
    assert b.get_position("600519").avg_cost > 10.0  # 含佣金
    Path(f).unlink(missing_ok=True)


def test_risk_multi_price_and_drawdown():
    rm = RiskManager(max_single_order_value=1e9, max_drawdown_pct=0.2)
    poss = {"A": Signal("A", SignalType.BUY, 100, 10.0)}
    from paper_trading.models import Position
    positions = {"AAA": Position("AAA", 1000, 1000, 10.0, datetime.now()),
                 "BBB": Position("BBB", 1000, 1000, 10.0, datetime.now())}
    sig = Signal(symbol="CCC", direction=SignalType.BUY, volume=100, price=10.0)
    ok, _ = rm.check_signal(sig, 10.0, 1e6, positions, 100000.0,
                            prices={"AAA": 10.0, "BBB": 10.0, "CCC": 10.0})
    assert ok
    rm.update_peak(100.0)
    halted, dd = rm.check_drawdown(79.0)
    assert halted and dd > 0.2
