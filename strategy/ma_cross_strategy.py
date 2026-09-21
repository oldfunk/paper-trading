"""双均线交叉策略。"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from paper_trading.models import Bar, Signal, SignalType
from paper_trading.strategy.base_strategy import BaseStrategy
from paper_trading.utils import get_logger

logger = get_logger(__name__)


class MACrossStrategy(BaseStrategy):
    """
    双均线交叉策略 (MA5/MA20)。

    规则：
    - 金叉（MA5 上穿 MA20）：买入
    - 死叉（MA5 下穿 MA20）：卖出
    """

    def __init__(
        self,
        short_window: int = 5,
        long_window: int = 20,
        buy_volume: int = 100,
        sell_volume: int = 100,
    ) -> None:
        super().__init__(name=f"MA_{short_window}_{long_window}")
        self.short_window = short_window
        self.long_window = long_window
        self.buy_volume = buy_volume
        self.sell_volume = sell_volume

    def generate_signals(self, bars: Dict[str, list[Bar]]) -> Dict[str, Signal]:
        signals: Dict[str, Signal] = {}
        for symbol, bar_list in bars.items():
            if len(bar_list) < self.long_window + 1:
                continue
            df = pd.DataFrame([
                {"close": b.close, "timestamp": b.timestamp} for b in bar_list
            ])
            df["ma_short"] = df["close"].rolling(self.short_window).mean()
            df["ma_long"] = df["close"].rolling(self.long_window).mean()

            # 扫描整个历史数据，找到最后一个交叉点
            last_signal: Optional[Signal] = None
            for i in range(1, len(df)):
                prev = df.iloc[i - 1]
                curr = df.iloc[i]

                # 金叉: 前一日 MA5 <= MA20, 当日 MA5 > MA20
                if prev["ma_short"] <= prev["ma_long"] and curr["ma_short"] > curr["ma_long"]:
                    last_signal = Signal(
                        symbol=symbol,
                        direction=SignalType.BUY,
                        volume=self.buy_volume,
                        price=curr["close"],
                        reason=f"Golden cross: MA{self.short_window} crossed above MA{self.long_window}",
                    )

                # 死叉: 前一日 MA5 >= MA20, 当日 MA5 < MA20
                elif prev["ma_short"] >= prev["ma_long"] and curr["ma_short"] < curr["ma_long"]:
                    last_signal = Signal(
                        symbol=symbol,
                        direction=SignalType.SELL,
                        volume=self.sell_volume,
                        price=curr["close"],
                        reason=f"Death cross: MA{self.short_window} crossed below MA{self.long_window}",
                    )

            if last_signal is not None:
                signals[symbol] = last_signal
                logger.info(
                    f"Signal: {last_signal.direction.name} {symbol} @ {last_signal.price:.2f} "
                    f"({last_signal.reason})"
                )

        return signals
