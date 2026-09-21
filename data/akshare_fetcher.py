"""akshare 数据采集器。"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd

from paper_trading.models import Bar
from paper_trading.utils import get_logger

logger = get_logger(__name__)


class AkshareFetcher:
    """使用 akshare 获取 A 股日线数据。"""

    @staticmethod
    def fetch_daily(
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjust: str = "qfq",
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ) -> list[Bar]:
        """
        获取单只股票日线数据（前复权），带重试机制。

        Args:
            symbol: 股票代码，如 '600519'
            start_date: 起始日期 'YYYYMMDD'，默认近3年
            end_date: 结束日期 'YYYYMMDD'，默认今天
            adjust: 复权方式，'qfq'=前复权, 'hfq'=后复权, ''=不复权
            max_retries: 最大重试次数
            retry_delay: 重试间隔（秒）

        Returns:
            Bar 列表
        """
        if not start_date:
            start_date = (datetime.now() - timedelta(days=365 * 3)).strftime("%Y%m%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")

        logger.info(f"Fetching {symbol} from {start_date} to {end_date} (adjust={adjust})")

        for attempt in range(1, max_retries + 1):
            try:
                # 优先使用新浪数据源（更稳定）
                sina_symbol = f"sh{symbol}" if symbol.startswith("6") else f"sz{symbol}"
                df = ak.stock_zh_a_daily(symbol=sina_symbol, start_date=start_date,
                                        end_date=end_date, adjust=adjust)
                if df.empty:
                    logger.warning(f"No data returned for {symbol}")
                    return []

                bars: list[Bar] = []
                for _, row in df.iterrows():
                    bars.append(Bar(
                        symbol=symbol,
                        timestamp=pd.to_datetime(row["date"]).to_pydatetime(),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=int(row["volume"]),
                        turn=float(row.get("turnover", 0.0)),
                    ))
                logger.info(f"Fetched {len(bars)} bars for {symbol}")
                return bars
            except Exception as e:
                if attempt < max_retries:
                    logger.warning(f"Attempt {attempt} failed for {symbol}: {e}. Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"All {max_retries} attempts failed for {symbol}: {e}")
                    raise

    @staticmethod
    def fetch_stock_pool(
        symbols: list[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict[str, list[Bar]]:
        """批量获取股票池数据。"""
        result: dict[str, list[Bar]] = {}
        for sym in symbols:
            try:
                result[sym] = AkshareFetcher.fetch_daily(sym, start_date, end_date)
            except Exception as e:
                logger.error(f"Failed to fetch {sym}: {e}")
                result[sym] = []
        return result
