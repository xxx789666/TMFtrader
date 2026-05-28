"""
Donchian Breakout 策略（vwap_fade §9 C1 後第一支 trend-following）。
  - _DonchianState   : N+K+1 根滑動視窗、entry 不含當前 bar、exit 含當前 bar
  - DonchianStrategy : 結構性突破進場 + opposite K-bar trailing 出場（Task P2b/P2c，待後續任務）
"""

from collections import deque
from datetime import datetime, time
from typing import Optional
import math

# Task P2b/P2c 進出場邏輯所需 import（待後續任務取消註解）
# from strategy.base import BaseStrategy, Signal, SignalDirection
# from core.market_data import KBar, MarketSnapshot
# from core.position import Position, Side


class _DonchianState:
    """Rolling Donchian state with lookahead-safe entry slicing.

    Attributes
    ----------
    warmup_ready : True 當 deque 有 > entry_n 根 bar
    entry_n_high : max(highs of bars [-entry_n-1 : -1])  <- **不含**當前 bar
    entry_n_low  : min(lows  of bars [-entry_n-1 : -1])
    exit_k_high  : max(highs of bars [-exit_k :])        <- **含**當前 bar
    exit_k_low   : min(lows  of bars [-exit_k :])
    跨日由 update() 內 _maybe_reset 自動清空 deque。
    """

    def __init__(self, entry_n: int, exit_k: int):
        if entry_n < 1:
            raise ValueError(f"entry_n must be >= 1, got {entry_n}")
        if exit_k < 1:
            raise ValueError(f"exit_k must be >= 1, got {exit_k}")
        self.entry_n = entry_n
        self.exit_k = exit_k
        cap = max(entry_n, exit_k) + 1
        self._highs: deque = deque(maxlen=cap)
        self._lows:  deque = deque(maxlen=cap)
        self._date = None

    def _maybe_reset(self, dt: datetime) -> None:
        d = dt.date()
        if d != self._date:
            self._date = d
            self._highs.clear()
            self._lows.clear()

    def update(self, dt: datetime, high: float, low: float) -> None:
        """收一根 K。每次呼叫先做換日 reset 偵測。"""
        self._maybe_reset(dt)
        self._highs.append(high)
        self._lows.append(low)

    @property
    def warmup_ready(self) -> bool:
        return len(self._highs) > self.entry_n

    @property
    def entry_n_high(self) -> float:
        if len(self._highs) <= self.entry_n:
            return float("nan")
        return max(list(self._highs)[-self.entry_n - 1 : -1])

    @property
    def entry_n_low(self) -> float:
        if len(self._lows) <= self.entry_n:
            return float("nan")
        return min(list(self._lows)[-self.entry_n - 1 : -1])

    @property
    def exit_k_high(self) -> float:
        if len(self._highs) == 0:
            return float("nan")
        return max(list(self._highs)[-self.exit_k :])

    @property
    def exit_k_low(self) -> float:
        if len(self._lows) == 0:
            return float("nan")
        return min(list(self._lows)[-self.exit_k :])


# -- DonchianStrategy 由 Task P2b/P2c 新增於此 ────────────────────────────────
