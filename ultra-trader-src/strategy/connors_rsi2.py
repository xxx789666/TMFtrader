"""
Connors RSI(2) 策略：日內極短期 RSI 均值回歸。
  - _RsiState           : Wilder smoothed RSI（連續，無跨日 reset）
  - ConnorsRsi2Strategy : 進出場（Task P2b/P2c，待後續任務新增）
"""

from datetime import datetime, time
from typing import Optional

# Task P2b/P2c 進出場邏輯所需 import（待後續任務取消註解）
# from strategy.base import BaseStrategy, Signal, SignalDirection
# from core.market_data import KBar, MarketSnapshot
# from core.position import Position, Side


class _RsiState:
    """Wilder smoothed RSI。

    呼叫 update(close) 餵價，property rsi 讀當前值。
    暖身期（update 數 < period）回 50.0（中性）。
    **沒有 reset()**：跨日連續累積，與 _SessionVwap（每日 reset）相反。
    """

    def __init__(self, period: int = 2):
        if period < 1:
            raise ValueError(f"period must be >= 1, got {period}")
        self.period = period
        self._prev_close: Optional[float] = None
        self._avg_gain = 0.0
        self._avg_loss = 0.0
        self._count = 0   # 已收到的 (價差) 樣本數

    def update(self, close: float) -> None:
        if self._prev_close is None:
            self._prev_close = close
            return
        change = close - self._prev_close
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        if self._count < self.period:
            # 初始期：累計簡單平均
            self._avg_gain += gain
            self._avg_loss += loss
            self._count += 1
            if self._count == self.period:
                self._avg_gain /= self.period
                self._avg_loss /= self.period
        else:
            # Wilder smoothed
            self._avg_gain = (self._avg_gain * (self.period - 1) + gain) / self.period
            self._avg_loss = (self._avg_loss * (self.period - 1) + loss) / self.period
        self._prev_close = close

    @property
    def rsi(self) -> float:
        if self._count < self.period:
            return 50.0
        if self._avg_loss < 1e-12:
            return 100.0
        rs = self._avg_gain / self._avg_loss
        return 100.0 - (100.0 / (1.0 + rs))


# ── ConnorsRsi2Strategy 由 Task P2b/P2c 新增於此 ───────────────────────────
