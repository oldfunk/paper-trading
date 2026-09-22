"""Hermes Agent 适配层。

提供 CLI 接口和 Python API，让 Hermes 的 agent 能力与定时任务机制
可以驱动 Paper Trading Framework 执行量化操作。

使用方式：
    # CLI 模式
    python -m paper_trading.hermes_bridge status
    python -m paper_trading.hermes_bridge run --symbols 600519
    python -m paper_trading.hermes_bridge buy --symbol 600519 --volume 100
    python -m paper_trading.hermes_bridge sell --symbol 600519 --volume 100
    python -m paper_trading.hermes_bridge nav
    python -m paper_trading.hermes_bridge history --limit 20

    # Cron 模式（由 Hermes cronjob 调用）
    python -m paper_trading.hermes_bridge cron-run --symbols 600519 000858

    # Agent 模式（返回 JSON，供 Hermes agent 解析）
    python -m paper_trading.hermes_bridge status --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# 确保项目根目录（paper_trading 包的上级）在 sys.path 中，兼容直接脚本运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paper_trading.broker.paper_broker import PaperBroker
from paper_trading.data.db_manager import DataDBManager
from paper_trading.models import Order, OrderType, TradingConfig
from paper_trading.portfolio.portfolio import Portfolio
from paper_trading.risk.risk_manager import RiskManager
from paper_trading.strategy.ma_cross_strategy import MACrossStrategy
from paper_trading.utils import get_logger


def _get_fetcher():
    from paper_trading.data.akshare_fetcher import AkshareFetcher

    return AkshareFetcher

logger = get_logger(__name__)


class HermesBridge:
    """
    Hermes Agent 适配层。

    职责：
    - 封装交易框架的启动、运行、查询操作
    - 提供 CLI 接口供 Hermes cronjob / agent 调用
    - 提供 JSON 输出供 Hermes agent 解析
    """

    def __init__(
        self,
        data_db: str = "data.db",
        account_db: str = "paper_account.db",
        config: Optional[TradingConfig] = None,
        config_path: Optional[str] = None,
    ) -> None:
        # config.yaml 单一真相源（显式 TradingConfig 优先，其次 config_path，其次默认 config.yaml）
        risk_kwargs: dict = {}
        strat_kwargs: dict = {"short_window": 5, "long_window": 20}
        if config is None:
            try:
                from paper_trading.utils.config import load_config

                cfg = load_config(config_path or "config.yaml")
                if cfg.get("raw"):
                    config = cfg["trading_config"]
                    risk_kwargs = cfg.get("risk", {})
                    strat_kwargs = cfg.get("strategy", {})
            except Exception:
                config = TradingConfig()
        self.data_db = DataDBManager(data_db)
        self.broker = PaperBroker(account_db, config)
        self.portfolio = Portfolio(self.broker)
        self.risk = RiskManager(**risk_kwargs) if risk_kwargs else RiskManager()
        self.strategy = MACrossStrategy(
            short_window=int(strat_kwargs.get("short_window", 5)),
            long_window=int(strat_kwargs.get("long_window", 20)),
            buy_volume=int(strat_kwargs.get("buy_volume", 100)),
            sell_volume=int(strat_kwargs.get("sell_volume", 100)),
        )
        self.config = config or TradingConfig()

    def run_daily(self, symbols: list[str]) -> dict:
        """
        执行每日结算流程（Run-Daily）。

        Args:
            symbols: 关注的股票列表

        Returns:
            执行结果摘要
        """
        logger.info(f"=== HermesBridge Run-Daily started at {datetime.now().isoformat()} ===")

        # 1. 更新行情
        try:
            Fetcher = _get_fetcher()
        except Exception as e:
            logger.error(f"Fetcher unavailable (offline mode): {e}")
            Fetcher = None
        for sym in symbols:
            try:
                if Fetcher is None:
                    continue
                latest = self.data_db.get_latest_timestamp(sym)
                start = latest[:10].replace("-", "") if latest else None
                bars = Fetcher.fetch_daily(sym, start_date=start)
                if bars:
                    self.data_db.upsert_bars(bars)
                    self.data_db.add_stock_to_pool(sym)
            except Exception as e:
                logger.error(f"Failed to update data for {sym}: {e}")

        # 1.5 刷新真实股票名称（全池 + 持仓，一次批量调用，失败保缓存）
        if Fetcher is not None:
            try:
                names_sym = set(symbols) | set(self.data_db.get_pool_symbols()) | \
                    {p.symbol for p in self.broker.get_all_positions()}
                fresh = Fetcher.fetch_stock_names(sorted(names_sym))
                if fresh:
                    self.data_db.upsert_stock_names(fresh)
            except Exception as e:
                logger.warning(f"Stock name refresh skipped: {e}")

        # 2. 获取最新K线并生成信号
        all_bars: dict[str, list] = {}
        latest_prices: dict[str, float] = {}
        for sym in symbols:
            bars = self.data_db.get_bars(sym, limit=30)
            if bars:
                all_bars[sym] = bars
                latest_prices[sym] = bars[-1].close

        # 3. T+1 结算（先解冻再交易）
        self.broker.unfreeze_t1()

        # 4. 策略信号
        signals = self.strategy.generate_signals(all_bars)
        logger.info(f"Generated {len(signals)} signals")

        # 5. 风控 + 撮合
        cash = self.broker.get_cash()
        positions = {p.symbol: p for p in self.broker.get_all_positions()}
        nav = self.broker.get_nav(latest_prices)
        self.risk.update_peak(nav.total_value)
        halted, dd = self.risk.check_drawdown(nav.total_value)
        if halted:
            logger.error(f"Drawdown halt: {dd:.2%}, skip trading")
            signals = {}

        executed_orders = []
        for symbol, signal in signals.items():
            ok, reason = self.risk.check_signal(
                signal, latest_prices.get(symbol, 0.0),
                cash, positions, nav.total_value, prices=latest_prices,
            )
            if not ok:
                logger.warning(f"Signal rejected by risk: {symbol} - {reason}")
                continue

            order = Order(
                symbol=signal.symbol,
                direction=signal.direction.value,
                volume=signal.volume,
                order_type=OrderType.LIMIT,
                limit_price=signal.price,
            )
            result = self.broker.submit_order(order)
            executed_orders.append({
                "symbol": symbol,
                "direction": "BUY" if signal.direction.value == 1 else "SELL",
                "volume": signal.volume,
                "price": result.filled_price,
                "status": result.status.value,
            })

        # 6. 记录 NAV
        self.portfolio.record_nav(latest_prices)

        # 7. 构建结果
        result = {
            "timestamp": datetime.now().isoformat(),
            "symbols": symbols,
            "signals_generated": len(signals),
            "orders_executed": len(executed_orders),
            "orders": executed_orders,
            "nav": self.broker.get_nav(latest_prices).__dict__,
            "positions": [
                {
                    "symbol": p.symbol,
                    "total_volume": p.total_volume,
                    "available_volume": p.available_volume,
                    "avg_cost": p.avg_cost,
                }
                for p in self.broker.get_all_positions()
            ],
        }

        logger.info("=== HermesBridge Run-Daily completed ===")
        return result

    def get_status(self) -> dict:
        """获取账户状态。"""
        positions = self.broker.get_all_positions()
        cash = self.broker.get_cash()

        # 获取最新价格
        latest_prices = {}
        for p in positions:
            bars = self.data_db.get_bars(p.symbol, limit=1)
            if bars:
                latest_prices[p.symbol] = bars[-1].close

        nav = self.broker.get_nav(latest_prices)

        return {
            "timestamp": datetime.now().isoformat(),
            "cash": cash,
            "market_value": nav.market_value,
            "total_value": nav.total_value,
            "pnl": nav.pnl,
            "pnl_pct": nav.pnl_pct,
            "positions": [
                {
                    "symbol": p.symbol,
                    "total_volume": p.total_volume,
                    "available_volume": p.available_volume,
                    "avg_cost": p.avg_cost,
                    "current_price": latest_prices.get(p.symbol, 0.0),
                    "market_value": p.total_volume * latest_prices.get(p.symbol, 0.0),
                }
                for p in positions
            ],
        }

    def place_order(
        self,
        symbol: str,
        direction: str,
        volume: int,
        price: Optional[float] = None,
    ) -> dict:
        """
        手动下单（强制走 RiskManager，与策略信号同等风控）。
        """
        if direction not in ("buy", "sell"):
            return {"ok": False, "error": f"Invalid direction {direction}, expect buy/sell"}
        if volume <= 0 or volume % 100 != 0:
            return {"ok": False, "error": f"Volume must be positive multiple of 100, got {volume}"}
        if price is None:
            bars = self.data_db.get_bars(symbol, limit=1)
            if not bars:
                return {"ok": False, "error": f"No data for {symbol}"}
            price = bars[-1].close

        # 名称缓存缺失时尝试补齐（真实名称，失败不阻断下单）
        if symbol not in self.data_db.get_stock_names():
            try:
                fresh = _get_fetcher().fetch_stock_names([symbol])
                if fresh:
                    self.data_db.upsert_stock_names(fresh)
            except Exception:
                pass

        from paper_trading.models import Signal, SignalType
        dir_value = 1 if direction == "buy" else -1
        signal = Signal(
            symbol=symbol,
            direction=SignalType.BUY if dir_value == 1 else SignalType.SELL,
            volume=volume,
            price=price,
            reason="manual",
        )
        # 风控前置
        positions = {p.symbol: p for p in self.broker.get_all_positions()}
        cash = self.broker.get_cash()
        latest = {symbol: price}
        for p in positions:
            b = self.data_db.get_bars(p, limit=1)
            if b:
                latest[p] = b[-1].close
        nav = self.broker.get_nav(latest)
        self.risk.update_peak(nav.total_value)
        halted, dd = self.risk.check_drawdown(nav.total_value)
        if halted:
            return {"ok": False, "error": f"Drawdown halt {dd:.2%}, order blocked"}
        ok, reason = self.risk.check_signal(signal, price, cash, positions, nav.total_value)
        if not ok:
            return {"ok": False, "error": f"Risk rejected: {reason}"}

        order = Order(
            symbol=symbol,
            direction=dir_value,
            volume=volume,
            order_type=OrderType.LIMIT,
            limit_price=price,
        )
        result = self.broker.submit_order(order)

        return {
            "ok": result.status.value == "filled",
            "order_id": result.order_id,
            "symbol": symbol,
            "direction": direction,
            "volume": volume,
            "price": result.filled_price,
            "status": result.status.value,
            "commission": result.commission,
            "stamp_duty": result.stamp_duty,
            "transfer_fee": result.transfer_fee,
        }

    def preview_order(
        self, symbol: str, direction: str, volume: int, price: Optional[float] = None
    ) -> dict:
        """Dry-run：只做风控+费用试算，不落库（供 agent 下单前调用）。"""
        if direction not in ("buy", "sell"):
            return {"ok": False, "error": "direction must be buy/sell"}
        if price is None:
            bars = self.data_db.get_bars(symbol, limit=1)
            if not bars:
                return {"ok": False, "error": f"No data for {symbol}"}
            price = bars[-1].close
        from paper_trading.models import Signal, SignalType

        signal = Signal(
            symbol=symbol,
            direction=SignalType.BUY if direction == "buy" else SignalType.SELL,
            volume=volume,
            price=price,
            reason="preview",
        )
        positions = {p.symbol: p for p in self.broker.get_all_positions()}
        cash = self.broker.get_cash()
        latest = {symbol: price}
        for p in positions:
            b = self.data_db.get_bars(p, limit=1)
            if b:
                latest[p] = b[-1].close
        nav = self.broker.get_nav(latest)
        ok, reason = self.risk.check_signal(signal, price, cash, positions, nav.total_value, prices=latest)
        amount = price * volume
        if direction == "buy":
            commission, transfer = self.broker._calc_buy_cost(amount)
            return {"ok": ok, "error": None if ok else f"Risk rejected: {reason}",
                    "estimate": {"amount": amount, "commission": commission,
                                 "transfer_fee": transfer, "total_cost": amount + commission + transfer}}
        commission, stamp, transfer = self.broker._calc_sell_cost(amount)
        return {"ok": ok, "error": None if ok else f"Risk rejected: {reason}",
                "estimate": {"amount": amount, "commission": commission,
                             "stamp_duty": stamp, "transfer_fee": transfer,
                             "net_proceeds": amount - commission - stamp - transfer}}

    def get_nav_history(self, limit: int = 30) -> list[dict]:
        """获取 NAV 历史。"""
        with self.broker._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM nav_history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_order_history(self, limit: int = 20) -> list[dict]:
        """获取订单历史。"""
        return self.broker.get_order_history(limit)

    def get_fill_history(self, limit: int = 20) -> list[dict]:
        """获取成交历史。"""
        return self.broker.get_fill_history(limit)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Paper Trading Framework - Hermes Agent Bridge"
    )
    parser.add_argument("--data-db", default="data.db", help="行情数据库路径")
    parser.add_argument("--account-db", default="paper_account.db", help="账户数据库路径")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--lock-file", default="paper_trading.lock", help="运行锁文件")

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # status
    status_parser = subparsers.add_parser("status", help="查看账户状态")
    status_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # run
    run_parser = subparsers.add_parser("run", help="执行每日结算流程")
    run_parser.add_argument("--symbols", nargs="+", default=["600519"], help="股票代码")
    run_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    run_parser.add_argument("--dry-run", action="store_true", help="只生成信号与风控预览，不下单")

    # buy
    buy_parser = subparsers.add_parser("buy", help="买入")
    buy_parser.add_argument("--symbol", required=True, help="股票代码")
    buy_parser.add_argument("--volume", type=int, required=True, help="数量")
    buy_parser.add_argument("--price", type=float, default=None, help="限价")
    buy_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # sell
    sell_parser = subparsers.add_parser("sell", help="卖出")
    sell_parser.add_argument("--symbol", required=True, help="股票代码")
    sell_parser.add_argument("--volume", type=int, required=True, help="数量")
    sell_parser.add_argument("--price", type=float, default=None, help="限价")
    sell_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # names
    names_parser = subparsers.add_parser("names", help="刷新并显示真实股票名称")
    names_parser.add_argument("--symbols", nargs="*", default=None,
                              help="缺省=股票池+持仓")
    names_parser.add_argument("--json", action="store_true")

    # preview
    preview_parser = subparsers.add_parser("preview", help="下单前风控+费用试算（不落库）")
    preview_parser.add_argument("--symbol", required=True)
    preview_parser.add_argument("--direction", choices=["buy", "sell"], required=True)
    preview_parser.add_argument("--volume", type=int, required=True)
    preview_parser.add_argument("--price", type=float, default=None)
    preview_parser.add_argument("--json", action="store_true")

    # nav
    nav_parser = subparsers.add_parser("nav", help="查看 NAV 历史")
    nav_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # history
    history_parser = subparsers.add_parser("history", help="查看历史记录")
    history_parser.add_argument("--type", choices=["orders", "fills"], default="orders")
    history_parser.add_argument("--limit", type=int, default=20)
    history_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # cron-run
    cron_parser = subparsers.add_parser("cron-run", help="Cron 模式执行")
    cron_parser.add_argument("--symbols", nargs="+", default=["600519"], help="股票代码")
    cron_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    args = parser.parse_args()

    def emit(data, ok: bool = True, error: str | None = None):
        print(json.dumps({"ok": ok, "data": data, "error": error},
                         indent=2, ensure_ascii=False, default=str))

    if not args.command:
        parser.print_help()
        sys.exit(2)

    from paper_trading.utils.run_lock import run_lock

    try:
        bridge = HermesBridge(data_db=args.data_db, account_db=args.account_db,
                              config_path=args.config)
    except Exception as e:
        emit(None, ok=False, error=f"Init failed: {e}")
        sys.exit(1)

    try:
        if args.command == "status":
            emit(bridge.get_status())

        elif args.command in ("run", "cron-run"):
            if getattr(args, "dry_run", False):
                # dry-run：信号+风控预览，不下单不记NAV
                all_bars = {}
                latest = {}
                for sym in args.symbols:
                    bars = bridge.data_db.get_bars(sym, limit=30)
                    if bars:
                        all_bars[sym] = bars
                        latest[sym] = bars[-1].close
                signals = bridge.strategy.generate_signals(all_bars)
                positions = {p.symbol: p for p in bridge.broker.get_all_positions()}
                nav = bridge.broker.get_nav(latest)
                preview = []
                for sym, sig in signals.items():
                    ok, reason = bridge.risk.check_signal(
                        sig, latest.get(sym, 0.0), bridge.broker.get_cash(),
                        positions, nav.total_value, prices=latest)
                    preview.append({"symbol": sym, "direction": sig.direction.name,
                                    "volume": sig.volume, "price": sig.price,
                                    "pass": ok, "reason": reason})
                bridge.broker.log_operation(
                    "run:dry-run", {"symbols": args.symbols}, True,
                    {"signals": len(preview)}, nav.cash, nav.total_value)
                emit({"signals": preview, "nav": nav.__dict__})
            else:
                with run_lock(args.lock_file):
                    res = bridge.run_daily(args.symbols)
                nav = res.get("nav") or {}
                bridge.broker.log_operation(
                    "run", {"symbols": args.symbols}, True,
                    {"signals": res.get("signals_generated"),
                     "orders": res.get("orders_executed")},
                    nav.get("cash"), nav.get("total_value"))
                emit(res)

        elif args.command == "buy":
            with run_lock(args.lock_file):
                r = bridge.place_order(args.symbol, "buy", args.volume, args.price)
                st = bridge.get_status()
                bridge.broker.log_operation(
                    "buy", {"symbol": args.symbol, "volume": args.volume,
                            "price": args.price},
                    r.get("ok", False),
                    {"status": r.get("status"), "error": r.get("error"),
                     "filled_price": r.get("price"), "commission": r.get("commission"),
                     "stamp_duty": r.get("stamp_duty"), "transfer_fee": r.get("transfer_fee")},
                    st["cash"], st["total_value"])
                emit(r, ok=r.get("ok", False), error=r.get("error"))
                if not r.get("ok"):
                    sys.exit(3)

        elif args.command == "sell":
            with run_lock(args.lock_file):
                r = bridge.place_order(args.symbol, "sell", args.volume, args.price)
                st = bridge.get_status()
                bridge.broker.log_operation(
                    "sell", {"symbol": args.symbol, "volume": args.volume,
                             "price": args.price},
                    r.get("ok", False),
                    {"status": r.get("status"), "error": r.get("error"),
                     "filled_price": r.get("price"), "commission": r.get("commission"),
                     "stamp_duty": r.get("stamp_duty"), "transfer_fee": r.get("transfer_fee")},
                    st["cash"], st["total_value"])
                emit(r, ok=r.get("ok", False), error=r.get("error"))
                if not r.get("ok"):
                    sys.exit(3)

        elif args.command == "names":
            targets = set(args.symbols or []) or (
                set(bridge.data_db.get_pool_symbols())
                | {p.symbol for p in bridge.broker.get_all_positions()}
            )
            fresh = {}
            if targets:
                try:
                    fresh = _get_fetcher().fetch_stock_names(sorted(targets))
                except Exception as e:
                    logger.warning(f"Name fetch failed (offline?): {e}")
                if fresh:
                    bridge.data_db.upsert_stock_names(fresh)
            cached = bridge.data_db.get_stock_names()
            merged = {s: cached.get(s) or fresh.get(s) or "" for s in sorted(targets)}
            emit({"names": merged, "source": "live" if fresh else "cache"})

        elif args.command == "preview":
            emit(bridge.preview_order(args.symbol, args.direction, args.volume, args.price))

        elif args.command == "nav":
            emit(bridge.get_nav_history())

        elif args.command == "history":
            if args.type == "orders":
                emit(bridge.get_order_history(args.limit))
            else:
                emit(bridge.get_fill_history(args.limit))
    except RuntimeError as e:
        emit(None, ok=False, error=str(e))
        sys.exit(4)
    except Exception as e:
        emit(None, ok=False, error=f"{type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
