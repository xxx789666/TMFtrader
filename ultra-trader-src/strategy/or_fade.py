"""
OR Fade 策略：開盤後區間 fade（vwap_fade 設計 §9 備案 C）。
  - _OrSession        : 開盤 N 根定區間、鎖後不變、跨日 reset
  - OrFadeStrategy    : 觸邊+量縮 fade 回 OR-mid（Task P2b/P2c，待後續任務新增）
"""

from datetime import datetime, time
from typing import Optional
import math

# Task P2b/P2c 進出場邏輯所需 import（待後續任務取消註解）
# from strategy.base import BaseStrategy, Signal, SignalDirection
# from core.market_data import KBar, MarketSnapshot
# from core.position import Position, Side


class _OrSession:
    """每日 OR 區間 state：收前 or_bars 根高低、鎖後不變、跨日重置。

    Attributes
    ----------
    locked  : OR 是否已鎖（已收滿 or_bars 根）
    or_high : 鎖後的 OR 上緣（鎖前回 nan）
    or_low  : 鎖後的 OR 下緣
    or_mid  : (or_high + or_low) / 2
    """

    def __init__(self, or_bars: int):
        if or_bars < 1:
            raise ValueError(f"or_bars must be >= 1, got {or_bars}")
        self.or_bars = or_bars
        self._date = None
        self._high = float("-inf")
        self._low  = float("inf")
        self._bar_count = 0
        self._locked = False

    def _maybe_reset(self, dt: datetime) -> None:
        d = dt.date()
        if d != self._date:
            self._date = d
            self._high = float("-inf")
            self._low  = float("inf")
            self._bar_count = 0
            self._locked = False

    def update(self, dt: datetime, high: float, low: float) -> None:
        """收一根 K；鎖後成 no-op。每次呼叫先做換日 reset 偵測。"""
        self._maybe_reset(dt)
        if self._locked:
            return
        if high > self._high:
            self._high = high
        if low < self._low:
            self._low = low
        self._bar_count += 1
        if self._bar_count >= self.or_bars:
            self._locked = True

    @property
    def locked(self) -> bool:
        return self._locked

    @property
    def or_high(self) -> float:
        return self._high if self._locked else float("nan")

    @property
    def or_low(self) -> float:
        return self._low if self._locked else float("nan")

    @property
    def or_mid(self) -> float:
        if not self._locked:
            return float("nan")
        return (self._high + self._low) / 2.0


# ── OrFadeStrategy 由 Task P2b/P2c 新增於此 ───────────────────────────────────
