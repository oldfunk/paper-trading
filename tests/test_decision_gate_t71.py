"""决策时点门的边界测试（T71）。

要防的是两类把同一件事改坏的方向，而且都改得动：
* **当日强交**：门一漏，16:49 做出的决定成交在当天 09:31 那根 bar 上
  ——成交价不再属于决策时点，账本与影子对账从此刻起系统性失真（P0-2 原貌）；
* **永不执行**：门写反/写死，`decided_at` 之后明明有 bar 却一直在 WAITING，
  意图挂到次日被 `expire_stale` 悄悄清掉——机会丢了且没人知道为什么。

现网口径的一个**冲突**（已写进 questions.md Q6，等主 AI 裁决）：
`queue.expire_stale` 按 `created_at` 的**日期**过期（"决策只在当日内有效"），
而 Batch 17/18 的话是"盘后入队 → 当日全 WAITING → **次日** bar 提交"。
11-05 现网那条 `002192_SELL_100@2026-11-05T15:09:20`（created_at=decided_at=15:09）
按现状会在 11-06 09:02 那一跳被 expired，等不到"明天开盘验证"。
本文件把**两端都钉住**：当日绝不强交、当日内有 bar 就必须交、跨日按现状过期；
裁决若改成"转次日"，只需要翻 `test_cross_day_post_close_intent_current_caliber`
一条，其余三条不动。
"""
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_trading.integration.rq_engine import queue as q         # noqa: E402
from paper_trading.integration.rq_engine.runner import (  # noqa: E402
    _SessionState, check_session_gates)


class _Ctx:
    def __init__(self, now):
        self.now = now


class _Bar:
    def __init__(self, close):
        self.close = close


class _BarDict(dict):
    """`bar_dict[ob]` 的语义照框架：没有这只标的就抛（runner 视为"这分钟没 bar"）。"""


def fake_order_shares(monkeypatch):
    """把 `rqalpha.api.order_shares` 换成记数桩：本地没有 rqalpha 也能跑 handle_bar。"""
    calls = []
    root = types.ModuleType("rqalpha")
    api = types.ModuleType("rqalpha.api")

    def order_shares(ob, vol):
        calls.append((ob, vol))
        return None                       # None ⇒ runner 记 NOT_CREATED（撮合由框架做）

    api.order_shares = order_shares
    root.api = api
    monkeypatch.setitem(sys.modules, "rqalpha", root)
    monkeypatch.setitem(sys.modules, "rqalpha.api", api)
    return calls, api


def _plan(biz, symbol, action, volume, decided_at):
    return [{"biz_id": biz, "symbol": symbol, "action": action,
             "volume": volume, "decided_at": decided_at}]


def _minutes(day, hh_mm, n):
    """从 hh:mm 起连续 n 分钟（模拟日内逐分钟事件；跨小时也得走）。"""
    d0 = datetime.strptime(f"{day} {hh_mm}", "%Y-%m-%d %H:%M")
    return [d0 + timedelta(minutes=i) for i in range(n)]


DAY = "2026-11-05"


def test_decide_boundary_equal_minute_is_go():
    """边界：`now == decided_at` 放行（这一分钟的成交价已经"在决定之后"）。"""
    d = datetime(2026, 11, 5, 14, 50)
    act = {"decided_at": d.isoformat()}
    assert _SessionState.decide(act, d.replace(minute=49)) == "wait"
    assert _SessionState.decide(act, d) == "go"
    assert _SessionState.decide(act, d.replace(minute=55)) == "go"
    # 解不出决策时刻 ⇒ 不门控（旧行为），但归因表里留下空串可查
    assert _SessionState.decide({"decided_at": "不是一段时间"}, d) == "go"
    assert _SessionState.decide({}, None) == "go"


def test_post_close_intent_never_fills_same_day(monkeypatch):
    """盘后（15:09）的决定，在当天 09:31–15:00 的 bar 上**一根都不许交**。"""
    calls, _ = fake_order_shares(monkeypatch)
    st = _SessionState(_plan("t71-a", "002192", "sell", 100,
                             f"{DAY}T15:09:20"))
    for now in _minutes(DAY, "09:31", 6) + _minutes(DAY, "14:55", 6):
        bd = _BarDict({"002192.XSHE": _Bar(50.0)})
        st.handle_bar(_Ctx(now), bd)
    assert calls == [] and st.submits == []
    assert [w["status"] for w in st.final_waiting()] == ["WAITING_DECISION"]
    # 只记一条 waiting（逐 bar 累积表会刷 240 条，归因表就不是给人看的了）
    assert len(st.waiting) == 1
    assert st.waiting[0]["decided_at"] == "2026-11-05 15:09:20"


def test_intraday_intent_submits_at_first_bar_after_decision(monkeypatch):
    """反方向：14:50 的决定，必须在**当天第一根 ≥14:50 且有 bar 的分钟**交出去。"""
    calls, _ = fake_order_shares(monkeypatch)
    st = _SessionState(_plan("t71-b", "002192", "sell", 100,
                             f"{DAY}T14:50:00"))
    for now in _minutes(DAY, "14:48", 8):
        bd = _BarDict({})                  # 默认这分钟没有 bar
        if now.minute in (50, 53):
            bd["002192.XSHE"] = _Bar(50.0)
        st.handle_bar(_Ctx(now), bd)
    assert calls == [("002192.XSHE", -100)], calls
    assert st.submits[0]["submitted_at"].startswith(f"{DAY} 14:50")
    assert st.final_waiting() == []        # 已定局，不再报"还在等"
    # 之后的 bar 重放同一意图：不能提交第二笔
    st.handle_bar(_Ctx(datetime(2026, 11, 5, 14, 53)),
                  _BarDict({"002192.XSHE": _Bar(50.1)}))
    assert len(calls) == 1


def test_no_bar_after_decision_means_waiting_not_silent_drop(monkeypatch):
    """决定之后一根 bar 都没有（当天已结束）⇒ 保持 WAITING，不许静默丢。"""
    calls, _ = fake_order_shares(monkeypatch)
    st = _SessionState(_plan("t71-c", "002192", "sell", 100,
                             f"{DAY}T15:00:30"))
    st.handle_bar(_Ctx(datetime(2026, 11, 5, 15, 0)),
                  _BarDict({"002192.XSHE": _Bar(50.0)}))
    assert calls == []
    assert st.final_waiting()[0]["biz_id"] == "t71-c"


def test_cross_day_post_close_intent_current_caliber(tmp_path):
    """跨日现状口径：`expire_stale` 按 `created_at` 的日期过期，不看是否盘后。

    这条就是 Q6 的靶子。裁决若为"盘后的决定转次日开盘"，被翻的应是这条
    （以及 `rq tick` 里 expire 的调用时机），上面四条**一条都不用动**。
    """
    db = str(tmp_path / "acct.db")
    q.enqueue(db, "002192", "sell", 100, rule_ref="t71")
    with q._conn(db) as c:                       # noqa: SLF001 - 把入队时刻搬到盘后
        c.execute("UPDATE rq_intents SET created_at=? WHERE symbol='002192'",
                  (f"{DAY}T15:09:20",))
    pending = q.pending(db)
    assert [i["decided_at"] for i in pending] == [f"{DAY}T15:09:20"]
    expired = q.expire_stale(db, "2026-11-06")
    assert len(expired) == 1
    assert q.pending(db) == []            # 次日会话再也拿不到它（不会 phantom 成交）


# ------------------------------------------------------------------ T+1 对齐 fail-closed
def _ev(applied, unsupported=()):
    return {"event_counts": {"BAR": 240, "SETTLEMENT": 1},
            "trades": [{"symbol": "000858.XSHE"}],
            "frozen_align": {"applied": applied},
            "init_align": {"unsupported": [{"symbol": s} for s in unsupported]}}


def _gate(evidence, frozen):
    return check_session_gates(evidence, [{"biz_id": "b", "symbol": "000858",
                                           "action": "buy", "volume": 100}],
                               handler_calls=240, init_frozen=frozen)


def test_gate_errors_when_freeze_left_no_trail():
    """账本有冻结、会话留痕里没有 ⇒ 停线（不落账、exit 1），不许"先跑着再说"。"""
    out = _gate(_ev([]), {"300390.XSHE": 200})
    assert out["ok"] is False
    assert any(e.startswith("T1FreezeMissing") and "300390" in e for e in out["errors"])


def test_gate_passes_when_freeze_recorded_even_unchanged():
    """续跑跳上冻结是从 state 带来的（changed=False），留痕在就算对齐过。"""
    assert _gate(_ev([{"symbol": "300390.XSHE", "from": 200, "to": 200,
                       "quantity": 200, "changed": False}]),
                 {"300390.XSHE": 200})["ok"] is True


def test_gate_ignores_symbols_the_framework_cannot_hold():
    """开源 bundle 没有北交所 .BJ：期初已被如实剔除，不该再报冻结缺失。"""
    assert _gate(_ev([], unsupported=("920156.BJ",)), {"920156.BJ": 100})["ok"] is True
    # 但"没剔除、也没对齐"必须响——否则这条豁免就成了万能挡箭牌
    assert _gate(_ev([]), {"920156.BJ": 100})["ok"] is False


def test_gate_silent_when_nothing_to_freeze():
    assert _gate(_ev([]), {})["ok"] is True
