"""一天的 rqalpha 模拟会话（一天 = 一个进程，G-T13-9）。

窗口语义来自 T13 取证：`SimulationEventSource.events(start,end,freq)` 只产出
落在 `[start,end]` 的交易日，`start==end==今日` 就恰好是一天
（`BEFORE_TRADING 00:00 → OPEN_AUCTION → BAR 15:00 → AFTER_TRADING 15:30 →
SETTLEMENT`，`simulation_event_source.py:90-104` + `executor.py:61-62`）。

本模块**不含撮合/计价/判定**：下单只调 rqalpha 公开 API，判定全在框架内。
门禁都在这里的**前置/后置检查**上：
G-T13-3 band（store 构造时拒供）、G-T13-5 幂等+指纹（SessionGate）、
G-T13-6 禁 sys_analyser、G-T13-7 空转断言、G-T13-8 拒单原因采集、
G-T13-9 一天一进程（模块级 `_SESSION_USED` 拒绝进程内重入）。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from paper_trading.utils import fees

from . import costs
from .provider import SessionGate, SessionGateError, config_fingerprint
from .store import to_order_book_id

PKG_DIR = Path(__file__).resolve().parent
MOD_ALIAS = "rqalpha_mod_ptpaper"          # rqalpha 的 mod 装载名（mod/__init__.py:54）


def _register_mod() -> None:
    """把 mod 入口以 `rqalpha_mod_ptpaper` 名字预注册进 sys.modules。

    不用 `sys.path.insert(包目录)`：那个目录曾叫 `rqalpha/`，注入后
    `import rqalpha` 会命中我们自己的包，把真框架遮蔽掉（实测报
    `No module named 'rqalpha.store'`）。`importlib.import_module`
    先查 sys.modules，所以注册即可，无需污染导入路径。
    """
    import importlib
    import sys as _sys

    if MOD_ALIAS not in _sys.modules:
        _sys.modules[MOD_ALIAS] = importlib.import_module(
            "paper_trading.integration.rq_engine.mod_entry")

INIT_CASH_DEFAULT = 100_000.0
#: 会话只能在本进程跑一次：rqalpha 的 `cleanup_resources()` 会把 Environment
#: 单例置 None，而 `utils/logger.py:38` 每条用户日志都要它（report-t13 §4.7）
_SESSION_USED = False


class IdleSpinError(RuntimeError):
    """G-T13-7：会话没跑到 handle_bar，或计划下单却既无成交也无拒单原因。"""


def build_config(*, today: str, data_db: str, bundle: str, symbols: list,
                 init_cash: float = INIT_CASH_DEFAULT,
                 state_dir: str = "", evidence: str = "",
                 require_bands: bool = True,
                 override_available_range: bool = True,
                 matching_type: str = "current_bar",
                 fee: Any = None,                  # TradingConfig 或四项费率 dict（T51）
                 require_today_reference: bool = True,
                 ledger: bool = False,
                 frequency: str = "1d",           # M3b："1m" = 5 分钟 bar 驱动分钟会话
                 extra: Optional[dict] = None) -> dict:
    """一个模拟日的 rqalpha 配置（纯函数，便于离线测试）。

    `fee` 是现网费率来源：**`TradingConfig` 或含四项费率的 dict**
    （`commission_rate / commission_min / stamp_duty_rate / transfer_fee_rate`）。
    T51 起**必须显式给**：缺项直接报错，不再有"不传就用现网默认值"这条路——
    那等于在 `costs.py` 里留第二份费率数字（改 config 引擎不跟着变的经典起点）。
    也绝不用框架默认：框架股票佣金是万八，现网是万 2.5（T38 实测口径差）。
    """
    persist = bool(state_dir)
    fee_map = fees.as_fee_dict(fee)
    transfer_fee_rate = fee_map.pop("transfer_fee_rate")
    cost_cfg = costs.stock_cost_mod_config(**fee_map)
    cfg = {
        "base": {
            "start_date": today,
            "end_date": today,
            "accounts": {"STOCK": float(init_cash)},
            "frequency": frequency,      # M3b："1m" 分钟会话（5 分钟 bar 驱动）
            "data_bundle_path": bundle,
            "capital_gain_tax_rate": 0.0,
            "locale": "zh_CN",
            "run_type": "p",                 # PAPER_TRADING：走 sys_simulation 内核
            "persist": persist,
            "persist_mode": "real_time",
        },
        "extra": {"log_level": "none", "enable_profiler": False, "context_vars": {}},
        "mod": {
            "sys_progress": {"enabled": False},
            # G-T13-6：跨进程续跑会重放昨日 SETTLEMENT（executor.py:68-79），
            # analyser 的日记录与基准序列长度不一致 → rqrisk/risk.py:30 assert 炸，
            # 一天跑完却按失败退出。指标继续由我们的 nav_history 记录器出。
            "sys_analyser": {"enabled": False},
            # T38 裁决 5：佣金/印花税钉到现网费率（框架默认万八，见 costs.py 模块头）
            "sys_transaction_cost": cost_cfg,
            "sys_simulation": {
                "enabled": True,
                "matching_type": matching_type,   # §5.4：raw 无成交额口径 → 只用 current_bar
                "price_limit": True,              # AGENTS 合规表 #4/#5/#6
                "volume_limit": True,
                "slippage": 0,
            },
            "ptpaper": {
                "enabled": True,
                "priority": 200,  # T55-R1：最后 start_up，此时框架 decider 已注册，
                                  # mod 自己的 start() 里可直接装过户费 overlay
                                  #（旧值 50 先于 sys_transaction_cost(100)，只能靠
                                  # POST_BEFORE_TRADING 事件装——resume 跳过该事件
                                  # ⇒ 续跑进程每笔少收过户费，T52-R1 钱类 bug）
                "db": str(Path(data_db).resolve()),
                "bundle": bundle,
                "today": today,
                "symbols": list(symbols),
                "require_bands": require_bands,
                "override_available_range": override_available_range,
                "state_dir": state_dir,
                "evidence": evidence,
                "pre_load": True,
                "transfer_fee_rate": transfer_fee_rate,   # 框架没有这项，mod 里补
                "require_today_reference": bool(require_today_reference),  # T50-C
                "ledger": bool(ledger),                   # T47-G1/G3：会话成交是否落现网账本
                "frequency": frequency,                   # M3b：mod 按此选日线/分钟 store
            },
        },
    }
    if extra:
        for k, v in extra.items():
            cfg[k] = v
    return cfg


def session_fingerprint(cfg: dict, intraday: bool = False) -> str:
    """会改变账本语义的配置指纹（漂移即拒续，G-T13-5）。

    `intraday=True`（M3b 日内多跳）：**排除 symbols**——每跳意图决定标的，
    池集合天然不同，不改账本语义（init_cash/费率/口径才是语义）。
    """
    base = cfg["base"]
    payload = {
        "accounts": base["accounts"],
        "run_type": base["run_type"],
        "frequency": base["frequency"],   # 1d 与 1m 的成交语义完全不同（R2）
        "capital_gain_tax_rate": base["capital_gain_tax_rate"],
        "matching_type": cfg["mod"]["sys_simulation"]["matching_type"],
        "price_limit": cfg["mod"]["sys_simulation"]["price_limit"],
        "slippage": cfg["mod"]["sys_simulation"]["slippage"],
        # T38：费率也改账本语义，必须进指纹（改了费率还想续跑昨天的状态 = 两本账混着算）
        "cost": cfg["mod"]["sys_transaction_cost"],
        "transfer_fee_rate": cfg["mod"]["ptpaper"]["transfer_fee_rate"],
        "require_today_reference": cfg["mod"]["ptpaper"]["require_today_reference"],
        # T47-G3：落不落现网账本是最大的语义差，必须进指纹（换了就得新会话）
        "ledger": cfg["mod"]["ptpaper"]["ledger"],
        "bundle": base["data_bundle_path"],
        "db": cfg["mod"]["ptpaper"]["db"],
        "require_bands": cfg["mod"]["ptpaper"]["require_bands"],
    }
    if not intraday:
        payload["symbols"] = sorted(cfg["mod"]["ptpaper"]["symbols"])
    return config_fingerprint(payload)


def check_session_gates(evidence: dict, plan: list, *, handler_calls: int,
                        settle_expected: int = 1,
                        expect_transfer_fee: bool = False) -> dict:
    """会话后置门禁（纯函数，可离线测）。"""
    counts = evidence.get("event_counts") or {}
    trades = evidence.get("trades") or []
    rejects = evidence.get("rejects") or []
    errors = []
    if handler_calls < 1:
        errors.append("IdleSpin: handle_bar 一次都没执行（窗口/事件源/数据没接上）")
    provider = evidence.get("provider") or {}
    if provider.get("restore_errors"):
        errors.append(f"PersistState: {provider['restore_errors']}")
    ordered = [p for p in plan if p.get("action") in ("buy", "sell") and p.get("volume")]
    if ordered and not trades and not rejects:
        errors.append("IdleSpin: 计划有下单，但既无成交也无拒单原因（静默失败）")
    # T38 裁决 5：要收过户费却没装上 = 费率口径悄悄回到"框架无过户费"，
    # 影子对账的 fee_delta 会假装归零，必须停线（框架哪天改了注册方式就是它先响）。
    if expect_transfer_fee:
        cost = evidence.get("cost") or {}
        if not cost.get("wrapped"):
            errors.append("TransferFeeNotInstalled: 配了过户费率，但没有一个合约 decider "
                          f"被套上 overlay（cost={cost}）")
    settlement = counts.get("SETTLEMENT", 0)
    warn = []
    if settlement > settle_expected:
        warn.append(f"SETTLEMENT 触发 {settlement} 次（预期 {settle_expected}）："
                    "跨进程续跑会重放昨日结算，账本无碍但指标不可信（G-T13-6）")
    return {"ok": not errors, "errors": errors, "warnings": warn,
            "fills": len(trades), "rejects": len(rejects),
            "event_counts": counts}


def describe_error(exc: BaseException) -> str:
    """把 rqalpha 的 ExceptionGroup 剥开，露出真因。

    `ModHandler.tear_down`（`mod/__init__.py:82-94`）在 mod 装载失败时会用
    `self._mod_dict[mod_name]` 再抛 `KeyError`，包装成
    `ExceptionGroup("Mod tear down failed")` —— 真因（比如 mod 导入失败）
    被埋在子异常里。纪律 4 要求大声，所以这里递归展开。
    """
    parts = []

    def walk(e, depth=0):
        if depth > 4:
            return
        msg = str(e).replace("\n", " / ")[:240]
        parts.append(f"{type(e).__name__}: {msg}")
        for sub in getattr(e, "exceptions", None) or []:
            walk(sub, depth + 1)
        cause = getattr(e, "__cause__", None)
        if cause is not None and cause is not e:
            walk(cause, depth + 1)

    walk(exc)
    return " | ".join(dict.fromkeys(parts))


def run_session(*, today: str, data_db: str, bundle: str, symbols: list, plan: list,
                init_cash: float = INIT_CASH_DEFAULT, state_dir: str = "",
                workdir: Optional[str] = None, force: bool = False,
                require_bands: bool = True, matching_type: str = "current_bar",
                fee: Any = None, require_today_reference: bool = True,
                ledger: bool = False, frequency: str = "1d",
                intraday_resume: bool = False,
                executed_ids: Optional[set] = None) -> dict:
    """跑一个模拟日（**G-T13-9：一个进程只允许一次**）。

    `intraday_resume=True`（M3b 日内多跳）：同日续跑放行（SessionGate intraday）。
    `executed_ids`：**意图幂等清单**（调用方从账本当日 orders 取，key 按
    `symbol[:6]_side_volume` 归一——T52-R4：plan 用 6 位码、框架成交是
    `000792.XSHE`，不归一就会重复下单）。handle_bar 跳过已执行意图。
    """
    global _SESSION_USED
    if _SESSION_USED:
        raise RuntimeError("G-T13-9：本进程已跑过一个 rqalpha 会话；"
                           "重启后 Environment 单例被置 None（main.py:301-307），"
                           "再入会让真实错误被 EnvironmentNotInitialized 掩盖。"
                           "常驻线请一天起一个进程。")
    _SESSION_USED = True

    work = Path(workdir or state_dir or ".")
    work.mkdir(parents=True, exist_ok=True)
    evidence_path = str(work / f"evidence_{today}.json")
    cfg = build_config(today=today, data_db=data_db, bundle=bundle, symbols=symbols,
                       init_cash=init_cash, state_dir=state_dir,
                       evidence=evidence_path, require_bands=require_bands,
                       matching_type=matching_type, fee=fee,
                       require_today_reference=require_today_reference,
                       ledger=ledger, frequency=frequency)
    fp = session_fingerprint(cfg, intraday=intraday_resume)

    gate = SessionGate(state_dir) if state_dir else None
    if gate:
        gate.check(date.fromisoformat(today), fp, force=force,
                   intraday_resume=intraday_resume)

    _register_mod()

    from rqalpha import run_func               # 只有真要跑时才需要 rqalpha

    global _STATE
    st = _SessionState(plan, executed_ids=set(executed_ids or ()))
    _STATE = st
    err = None
    try:
        run_func(config=cfg, before_trading=before_trading,
                 handle_bar=handle_bar, after_trading=after_trading)
    except BaseException as exc:            # noqa: BLE001 - 归因后如实返回信息
        err = describe_error(exc)
    evidence = (json.loads(Path(evidence_path).read_text(encoding="utf-8"))
                if Path(evidence_path).exists() else {})
    verdict = check_session_gates(evidence, plan, handler_calls=len(st.handler_dates),
                                  settle_expected=1 if not state_dir else 1,
                                  expect_transfer_fee=cfg["mod"]["ptpaper"]["transfer_fee_rate"] > 0)
    done = False
    if gate and err is None and verdict["ok"]:
        gate.mark_done(date.fromisoformat(today), fp,
                       {"fills": verdict["fills"], "rejects": verdict["rejects"]})
        done = True
    return {
        "today": today, "plan": plan, "error": err, "fingerprint": fp,
        "handler_dates": st.handler_dates, "orders": st.orders,
        "snapshots": st.snapshots, "evidence": evidence, "gates": verdict,
        "session_marked_done": done, "evidence_path": evidence_path,
    }


class _SessionState:
    """策略侧：只调公开 API 下单 + 记账号快照（不做任何判定）。

    注意：rqalpha 的 `UserFuncStrategyLoader` 用 `getfullargspec` 数参数，
    传**绑定方法**会多算一个 `self` 导致 `before_trading() takes 2 arguments
    but 3 were given`。所以这里必须由模块级函数转发（下面三个），
    本对象只当状态容器。
    """

    def __init__(self, plan: list, executed_ids: Optional[set] = None) -> None:
        self.plan = plan or []
        #: T52-R4 意图幂等：key = `symbol[:6]_side_volume`（6 位码归一——
        #: plan 用 '000792'、框架成交是 '000792.XSHE'，不归一过滤会失效重复下单）
        self.executed_ids = set(executed_ids or ())
        self.handler_dates: list = []
        self.orders: list = []
        self.snapshots: list = []

    @staticmethod
    def intent_key(symbol, side: str, volume: int) -> str:
        code = str(symbol).split(".")[0][:6].zfill(6)
        return f"{code}_{side.upper()}_{int(volume)}"

    def _snap(self, context, tag: str) -> dict:
        from rqalpha.const import POSITION_DIRECTION

        stock = context.portfolio.stock_account
        out = {"tag": tag, "dt": str(context.now),
               "cash": round(float(stock.cash), 4),
               "frozen_cash": round(float(stock.frozen_cash), 4),
               "total_value": round(float(stock.total_value), 4),
               "positions": {}}
        for ob, dirs in list(stock._positions.items()):
            p = dirs[POSITION_DIRECTION.LONG]
            out["positions"][ob] = {"quantity": int(p.quantity),
                                    "closable": int(p.closable),
                                    "avg_price": round(float(p.avg_price), 4),
                                    "last_price": round(float(p.last_price), 4)}
        return out

    def before_trading(self, context):
        self.snapshots.append(self._snap(context, "before_trading"))

    def handle_bar(self, context, bar_dict):
        from rqalpha.api import order_shares

        self.handler_dates.append(str(context.now)[:10])
        for act in self.plan:
            if act.get("action") not in ("buy", "sell") or not act.get("volume"):
                continue
            side = act["action"].upper()
            vol_i = int(act["volume"])
            # 意图幂等（T52-R4）：账本当日已执行过的意图直接跳过——日内多跳
            # 续跑时防重复下单的第一道闸（第二道 = SessionGate intraday）
            key = self.intent_key(act["symbol"], side, vol_i)
            if key in self.executed_ids:
                self.orders.append({"symbol": to_order_book_id(act["symbol"]),
                                    "side": side, "quantity": vol_i,
                                    "status": "SKIPPED_EXECUTED", "filled": 0})
                continue
            ob = to_order_book_id(act["symbol"])
            # 5 分钟对齐空档：rqalpha 1m 事件按日历逐分钟，5 分钟 bar 只在
            # :35/:40/... 落地——无当根 bar 时下单会抛空消息 CustomException。
            # BarMap 用 [] 访问（无 .get；不存在抛 KeyError）。
            # 跳过等下一根（**不记 executed**，有 bar 的那跳再提交）
            try:
                bar = bar_dict[ob]
            except Exception:                 # noqa: BLE001 - KeyError/AttributeError 均视为无 bar
                bar = None
            # 只用 close（BarObject 保证有；last_price 走 __getattr__ 会抛
            # CustomException 而非 AttributeError，getattr default 兜不住）
            bp = float(getattr(bar, "close", 0) or 0) if bar is not None else 0.0
            if bar is None or not bp:
                continue
            vol = vol_i * (1 if act["action"] == "buy" else -1)
            o = order_shares(ob, vol)
            self.executed_ids.add(key)          # 提交即记（防同跳内重复提交）
            if o is None:
                # 前端风控挡下：没有 Order 对象，原因在 mod 的事件采集里
                self.orders.append({"symbol": ob,
                                    "side": "BUY" if vol > 0 else "SELL",
                                    "quantity": abs(vol), "status": "NOT_CREATED",
                                    "filled": 0})
                continue
            for od in (o if isinstance(o, (list, tuple)) else [o]):
                self.orders.append({
                    "symbol": od.order_book_id,
                    "side": str(getattr(od.side, "value", od.side)).upper(),
                    "quantity": int(od.quantity),
                    "status": str(getattr(od.status, "value", od.status)).upper(),
                    "filled": int(od.filled_quantity),
                    "message": str(od.message)})

    def after_trading(self, context):
        self.snapshots.append(self._snap(context, "after_trading"))


_STATE: Optional[_SessionState] = None


def before_trading(context):
    _STATE.before_trading(context)


def handle_bar(context, bar_dict):
    _STATE.handle_bar(context, bar_dict)


def after_trading(context):
    _STATE.after_trading(context)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


__all__ = ["run_session", "build_config", "session_fingerprint", "check_session_gates",
           "IdleSpinError", "SessionGate", "SessionGateError", "now_iso"]
