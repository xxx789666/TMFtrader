"""
VWAP Fade 策略（日內 VWAP 帶狀均值回歸）
==========================================
架構：
  - _SessionVwap   : 每日 session VWAP + 偏離 σ（此檔 Task P2a）
  - VwapFadeStrategy: 進出場邏輯（Task P2b/P2c，待後續任務新增）

設計原則：
  * 換日偵測由呼叫端傳入的 datetime 日期自動完成，無須外部重置
  * sigma 採樣本標準差（ddof=1）
  * update_close_proxy 用於 held bar（只有收盤價時），成交量照算不漏
"""

import math
from collections import deque
from datetime import datetime
from typing import Optional

# ── Task P2b/P2c 進場/出場邏輯所需 import（待後續任務新增）──────────────────
# from strategy.base import BaseStrategy, Signal, SignalDirection
# from core.market_data import KBar, MarketSnapshot
# from core.position import Position, Side
# ────────────────────────────────────────────────────────────────────────────


class _SessionVwap:
    """每日 reset 的 session VWAP + 偏離 σ。

    換日由呼叫端傳入的日期自動偵測：第一筆 dt 日期記下後，
    後續 dt.date() 不同即觸發 reset。

    Attributes
    ----------
    vwap        : 當日累積 VWAP
    sigma       : 收盤價對 VWAP 偏離的樣本標準差（最近 sigma_window 根 bar）
    session_bar : 當日累積 bar 數（reset 後從 0 起算）
    """

    def __init__(self, sigma_window: int = 20, use_volume: bool = True):
        self.sigma_window = sigma_window
        self.use_volume = use_volume
        self._date = None
        self._cum_pv = 0.0   # Σ(typical × v)
        self._cum_v = 0.0    # Σv
        self._dev: Optional[deque] = (
            deque(maxlen=sigma_window) if sigma_window > 0 else None
        )
        self.session_bar = 0

    # ── 內部方法 ──────────────────────────────────────────────────────────

    def _maybe_reset(self, dt: datetime) -> None:
        d = dt.date()
        if d != self._date:
            self._date = d
            self._cum_pv = 0.0
            self._cum_v = 0.0
            if self._dev is not None:
                self._dev.clear()
            self.session_bar = 0

    def _accumulate(self, typical: float, volume: float, close: float) -> None:
        v = volume if (self.use_volume and volume > 0) else 1.0
        self._cum_pv += typical * v
        self._cum_v += v
        self.session_bar += 1
        # 偏離使用 post-update VWAP（先更新累計，再算偏離）
        if self._dev is not None:
            self._dev.append(close - self.vwap)

    # ── 公開 API ──────────────────────────────────────────────────────────

    def update(
        self,
        dt: datetime,
        high: float,
        low: float,
        close: float,
        volume: float,
    ) -> None:
        """完整 bar 更新：typical = (H + L + C) / 3"""
        self._maybe_reset(dt)
        self._accumulate((high + low + close) / 3.0, volume, close)

    def update_close_proxy(
        self,
        dt: datetime,
        close: float,
        volume: float,
    ) -> None:
        """Held bar 更新：只有收盤價，以 close 作為 typical，成交量照計。"""
        self._maybe_reset(dt)
        self._accumulate(close, volume, close)

    @property
    def vwap(self) -> float:
        """當日累積 VWAP；尚無資料時回傳 0.0。"""
        return self._cum_pv / self._cum_v if self._cum_v > 0 else 0.0

    @property
    def sigma(self) -> float:
        """收盤偏離 VWAP 的樣本標準差（ddof=1）；不足 2 根 bar 時回傳 0.0。"""
        if self._dev is None or len(self._dev) < 2:
            return 0.0
        m = sum(self._dev) / len(self._dev)
        return math.sqrt(
            sum((x - m) ** 2 for x in self._dev) / (len(self._dev) - 1)
        )


# ── VwapFadeStrategy 將由 Task P2b/P2c 新增於此 ──────────────────────────────
