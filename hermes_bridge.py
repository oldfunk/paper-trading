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

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

from paper_trading.broker.paper_broker import PaperBroker
from paper_trading.data.akshare_fetcher import AkshareFetcher
from paper_trading.data.db_manager import DataDBManager
from paper_trading.models import Order, OrderType, TradingConfig
from paper_trading.portfolio.portfolio import Portfolio
from paper_trading.risk.risk_manager import RiskManager
from paper_trading.strategy.ma_cross_strategy import MACrossStrategy
from paper_trading.utils import get_logger

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
    ) -> None:
        self.data_db = DataDBManager(data_db)
        self.broker = PaperBroker(account_db, config)
        self.portfolio = Portfolio(self.broker)
        self.risk = RiskManager()
        self.strategy = MACrossStrategy(short_window=5, long_window=20)
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
        for sym in symbols:
            try:
                latest = self.data_db.get_latest_timestamp(sym)
                start = latest[:10].replace("-", "") if latest else None
                bars = AkshareFetcher.fetch_daily(sym, start_date=start)
                if bars:
                    self.data_db.upsert_bars(bars)
                    self.data_db.add_stock_to_pool(sym)
            except Exception as e:
                logger.error(f"Failed to update data for {sym}: {e}")

        # 2. 获取最新K线并生成信号
        all_bars: dict[str, list] = {}
        latest_prices: dict[str, float] = {}
        for sym in symbols:
            bars = self.data_db.get_bars(sym, limit=30)
            if bars:
                all_bars[sym] = bars
                latest_prices[sym] = bars[-1].close

        # 3. 策略信号
        signals = self.strategy.generate_signals(all_bars)
        logger.info(f"Generated {len(signals)} signals")

        # 4. 风控 + 撮合
        cash = self.broker.get_cash()
        positions = {p.symbol: p for p in self.broker.get_all_positions()}
        nav = self.broker.get_nav(latest_prices)
        self.risk.update_peak(nav.total_value)

        executed_orders = []
        for symbol, signal in signals.items():
            ok, reason = self.risk.check_signal(
                signal, latest_prices.get(symbol, 0.0),
                cash, positions, nav.total_value,
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

        # 5. T+1 结算
        self.broker.unfreeze_t1()

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
        手动下单。

        Args:
            symbol: 股票代码
            direction: 'buy' 或 'sell'
            volume: 数量
            price: 限价（None 则用最新收盘价）

        Returns:
            订单结果
        """
        if price is None:
            bars = self.data_db.get_bars(symbol, limit=1)
            if not bars:
                return {"error": f"No data for {symbol}"}
            price = bars[-1].close

        dir_value = 1 if direction == "buy" else -1
        order = Order(
            symbol=symbol,
            direction=dir_value,
            volume=volume,
            order_type=OrderType.LIMIT,
            limit_price=price,
        )
        result = self.broker.submit_order(order)

        return {
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
    parser.add_argument(
        "--data-db", default="data.db", help="行情数据库路径"
    )
    parser.add_argument(
        "--account-db", default="paper_account.db", help="账户数据库路径"
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # status
    status_parser = subparsers.add_parser("status", help="查看账户状态")
    status_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # run
    run_parser = subparsers.add_parser("run", help="执行每日结算流程")
    run_parser.add_argument("--symbols", nargs="+", default=["600519"], help="股票代码")
    run_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

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

    if not args.command:
        parser.print_help()
        sys.exit(1)

    bridge = HermesBridge(
        data_db=args.data_db,
        account_db=args.account_db,
    )

    result = None

    if args.command == "status":
        result = bridge.get_status()

    elif args.command == "run":
        result = bridge.run_daily(args.symbols)

    elif args.command == "buy":
        result = bridge.place_order(args.symbol, "buy", args.volume, args.price)

    elif args.command == "sell":
        result = bridge.place_order(args.symbol, "sell", args.volume, args.price)

    elif args.command == "nav":
        result = bridge.get_nav_history()

    elif args.command == "history":
        if args.type == "orders":
            result = bridge.get_order_history(args.limit)
        else:
            result = bridge.get_fill_history(args.limit)

    elif args.command == "cron-run":
        result = bridge.run_daily(args.symbols)

    if result is not None:
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
