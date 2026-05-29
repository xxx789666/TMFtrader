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
from datetime import datetime, time
from typing import Optional

# ── Task P2b/P2c 進場/出場邏輯所需 import ────────────────────────────────────
from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side
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


# ── VwapFadeStrategy（Task P2b：進場邏輯 on_kbar）────────────────────────────

class VwapFadeStrategy(BaseStrategy):
    """日內 VWAP 帶狀均值回歸策略（進場邏輯）。

    進場條件（同時成立）：
      1. 預熱足夠（session_bar >= min_warmup）
      2. 時間在 entry_window 內
      3. 當日交易次數 < max_trades
      4. 不在 cooldown 期
      5. ADX <= adx_max（非強趨勢）
      6. sigma > 0（有足夠偏離資訊）
      7. close <= vwap - k*sigma（多單）或 close >= vwap + k*sigma（空單）

    出場邏輯（stop/TP/force_close）由 Task P2c 實作。
    """

    def __init__(
        self,
        k: float = 2.0,
        k2: float = 3.0,
        sigma_window: int = 20,
        adx_max: float = 35.0,
        sl_atr: float = 2.0,
        max_bars: int = 24,
        max_trades: int = 6,
        cooldown: int = 3,
        min_warmup: int = 3,
        entry_window: tuple = ("09:00", "13:00"),
        force_close: str = "13:25",
        use_volume: bool = True,
        point_value: float = 10.0,
    ):
        self.k = k
        self.k2 = k2
        self.adx_max = adx_max
        self.sl_atr = sl_atr
        self.max_bars = max_bars
        self.max_trades = max_trades
        self.cooldown = cooldown
        self.min_warmup = min_warmup
        self.point_value = point_value
        self._ew_start = time.fromisoformat(entry_window[0])
        self._ew_end = time.fromisoformat(entry_window[1])
        self._force_close = time.fromisoformat(force_close)
        self._vwap = _SessionVwap(sigma_window=sigma_window, use_volume=use_volume)
        self._trades_today = 0
        self._cooldown_until_bar = -1
        self._day = None
        self._last_adx = 0.0

    @property
    def name(self) -> str:
        return "vwap_fade"

    def _maybe_daily_reset(self, dt: datetime) -> None:
        """換日時重置交易計數與 cooldown 狀態。"""
        if dt.date() != self._day:
            self._day = dt.date()
            self._trades_today = 0
            self._cooldown_until_bar = -1

    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot) -> Optional[Signal]:
        """每根 K 棒收盤時呼叫；符合條件時回傳進場 Signal，否則回傳 None。"""
        ts = kbar.datetime
        self._maybe_daily_reset(ts)
        self._vwap.update(ts, kbar.high, kbar.low, kbar.close, kbar.volume)
        self._last_adx = snapshot.adx

        # ── 進場過濾器 ──────────────────────────────────────────────────────
        if self._vwap.session_bar < self.min_warmup:
            return None
        if not (self._ew_start <= ts.time() < self._ew_end):
            return None
        if self._trades_today >= self.max_trades:
            return None
        if self._vwap.session_bar <= self._cooldown_until_bar:
            return None
        if snapshot.adx > self.adx_max:
            return None

        sigma = self._vwap.sigma
        if sigma <= 0:
            return None

        vwap = self._vwap.vwap
        close = kbar.close
        atr = max(snapshot.atr, 1.0)
        upper = vwap + self.k * sigma
        lower = vwap - self.k * sigma

        # ── 多單：收盤跌破下軌 ─────────────────────────────────────────────
        if close <= lower:
            sl_band = vwap - self.k2 * sigma
            sl_atrp = close - self.sl_atr * atr
            # 二擇緊者，但停損必須在進場價的保護側（多單：< close）。
            # 深偏離時 sl_band 可能 >= close（無效）→ 排除後在剩下的候選中取較緊（max for long）。
            candidates = [s for s in (sl_band, sl_atrp) if s < close]
            stop = max(candidates)   # sl_atrp 必 < close（atr,sl_atr>0），candidates 不會空
            return self._signal(SignalDirection.BUY, close, stop, vwap, sigma)

        # ── 空單：收盤突破上軌 ─────────────────────────────────────────────
        if close >= upper:
            sl_band = vwap + self.k2 * sigma
            sl_atrp = close + self.sl_atr * atr
            # 二擇緊者，但停損必須在進場價的保護側（空單：> close）。
            # 深偏離時 sl_band 可能 <= close（無效）→ 排除後在剩下的候選中取較緊（min for short）。
            candidates = [s for s in (sl_band, sl_atrp) if s > close]
            stop = min(candidates)   # sl_atrp 必 > close（atr,sl_atr>0），candidates 不會空
            return self._signal(SignalDirection.SELL, close, stop, vwap, sigma)

        return None

    def _signal(
        self,
        direction: SignalDirection,
        price: float,
        stop: float,
        vwap: float,
        sigma: float,
    ) -> Signal:
        """建構並回傳 Signal；同時遞增當日交易計數（entry-side 過交易保護）。"""
        self._trades_today += 1   # REFINEMENT P2b: 在此遞增，確保 max_trades guard 有效
        return Signal(
            direction=direction,
            strength=1.0,
            stop_loss=round(stop, 1),
            take_profit=round(vwap, 1),
            reason=(
                f"vwap_fade {direction.value} "
                f"dev={price - vwap:+.1f} "
                f"sigma={sigma:.2f} "
                f"adx={self._last_adx:.0f}"
            ),
            source=self.name,
        )

    # ── Task P2c: 出場邏輯 ──────────────────────────────────────────────────

    def check_exit(self, position: "Position", snapshot: "MarketSnapshot") -> Optional[Signal]:
        """每根 bar 持倉期間呼叫；回傳出場 Signal 或 None。

        決策順序：
          1. 盤末強平（snapshot.timestamp >= force_close）
          2. 凍結停損（同時設冷卻）
          3. 移動 VWAP 停利
          4. 時間停損（bars_since_entry > max_bars）
        """
        if position.is_flat:
            return None

        ts = snapshot.timestamp          # 真實 bar 時間（不可用 self._current_bar_time）
        price = snapshot.price
        self._vwap.update_close_proxy(ts, price, snapshot.volume)
        is_long = (position.side == Side.LONG)

        def close_sig(reason: str) -> Signal:
            return Signal(
                direction=SignalDirection.CLOSE,
                strength=1.0,
                stop_loss=0,
                take_profit=0,
                reason=reason,
                source=self.name,
            )

        # 1) 盤末強平
        if ts.time() >= self._force_close:
            return close_sig(f"盤末強平 @ {price:.0f}")

        # 2) 凍結停損（觸發時同時寫入冷卻）
        if position.stop_loss > 0:
            if is_long and price <= position.stop_loss:
                self._cooldown_until_bar = self._vwap.session_bar + self.cooldown
                return close_sig(f"停損 @ {price:.0f}")
            if (not is_long) and price >= position.stop_loss:
                self._cooldown_until_bar = self._vwap.session_bar + self.cooldown
                return close_sig(f"停損 @ {price:.0f}")

        # 3) 移動 VWAP 停利
        vwap = self._vwap.vwap
        if vwap > 0:
            if is_long and price >= vwap:
                return close_sig(f"回到VWAP停利 @ {price:.0f}")
            if (not is_long) and price <= vwap:
                return close_sig(f"回到VWAP停利 @ {price:.0f}")

        # 4) 時間停損
        if position.bars_since_entry > self.max_bars:
            return close_sig(f"時間停損 {position.bars_since_entry}根")

        return None

    def get_parameters(self) -> dict:
        """回傳可調整的策略參數（供 Dashboard 顯示）。"""
        return {
            "k": self.k,
            "k2": self.k2,
            "adx_max": self.adx_max,
            "max_bars": self.max_bars,
            "max_trades": self.max_trades,
        }

    def reset(self):
        """重置策略狀態（新交易日）；保留 sigma_window 與 use_volume 設定。"""
        self._vwap = _SessionVwap(
            sigma_window=self._vwap.sigma_window,
            use_volume=self._vwap.use_volume,
        )
        self._trades_today = 0
        self._cooldown_until_bar = -1
        self._day = None
