"""
OR Fade 策略：開盤後區間 fade（vwap_fade 設計 §9 備案 C）。
  - _OrSession        : 開盤 N 根定區間、鎖後不變、跨日 reset
  - OrFadeStrategy    : 觸邊+量縮 fade 回 OR-mid（Task P2b/P2c，待後續任務新增）
"""

from datetime import datetime, time
from typing import Optional
import math

# Task P2b/P2c 進出場邏輯所需 import
from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side


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


# ── OrFadeStrategy ───────────────────────────────────────────────────────────

class OrFadeStrategy(BaseStrategy):
    def __init__(
        self,
        or_bars: int = 6,
        vol_ratio_max: float = 0.7,
        wait_bars: int = 0,
        sl_atr: float = 2.0,
        max_bars: int = 18,
        cooldown: int = 3,
        max_trades: int = 6,
        entry_window_end: str = "12:00",
        force_close: str = "13:25",
        allow_short: bool = False,
        point_value: float = 10.0,
    ):
        if wait_bars not in (0, 1):
            raise ValueError(f"wait_bars must be 0 or 1, got {wait_bars}")
        self.or_bars = or_bars
        self.vol_ratio_max = vol_ratio_max
        self.wait_bars = wait_bars
        self.sl_atr = sl_atr
        self.max_bars = max_bars
        self.cooldown = cooldown
        self.max_trades = max_trades
        self.allow_short = allow_short
        self.point_value = point_value

        # _ew_start 動態 = 08:45 + or_bars*5min
        ew_start_min = 8 * 60 + 45 + or_bars * 5
        self._ew_start = time(ew_start_min // 60, ew_start_min % 60)
        self._ew_end = time.fromisoformat(entry_window_end)
        self._force_close = time.fromisoformat(force_close)

        self._or = _OrSession(or_bars=or_bars)
        self._trades_today = 0
        self._cooldown_until_bar = -1
        self._session_bar = 0
        self._day = None
        self._pending_long: Optional[dict] = None
        self._pending_short: Optional[dict] = None

    @property
    def name(self) -> str:
        return "or_fade"

    def _maybe_daily_reset(self, dt: datetime) -> None:
        d = dt.date()
        if d != self._day:
            self._day = d
            self._trades_today = 0
            self._cooldown_until_bar = -1
            self._session_bar = 0
            self._pending_long = None
            self._pending_short = None
            # _or has its own _maybe_reset on update — no need to call here

    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot) -> Optional[Signal]:
        ts = kbar.datetime
        self._maybe_daily_reset(ts)
        self._or.update(ts, kbar.high, kbar.low)
        self._session_bar += 1

        # 守衛
        if not self._or.locked:
            return None
        if not (self._ew_start <= ts.time() < self._ew_end):
            return None
        if self._trades_today >= self.max_trades:
            if self.wait_bars == 1:
                self._pending_long = None
                self._pending_short = None
            return None
        if self._session_bar <= self._cooldown_until_bar:
            if self.wait_bars == 1:
                self._pending_long = None
                self._pending_short = None
            return None

        atr = max(snapshot.atr, 1.0)
        close = kbar.close

        # wait_bars=1：先處理 pending（前一根記下的）
        if self.wait_bars == 1:
            pending_long = self._pending_long
            pending_short = self._pending_short
            self._pending_long = None
            self._pending_short = None
            if pending_long is not None:
                if kbar.low > pending_long["touch_low"]:
                    stop = close - self.sl_atr * atr
                    return self._signal(SignalDirection.BUY, close, stop)
            if pending_short is not None:
                if kbar.high < pending_short["touch_high"]:
                    stop = close + self.sl_atr * atr
                    return self._signal(SignalDirection.SELL, close, stop)

        # 新觸邊偵測（vol_ratio 過濾）
        vol_ratio = snapshot.volume_ratio
        if vol_ratio > self.vol_ratio_max:
            return None

        if kbar.low <= self._or.or_low:
            if self.wait_bars == 0:
                stop = close - self.sl_atr * atr
                return self._signal(SignalDirection.BUY, close, stop)
            else:
                self._pending_long = {"touch_low": kbar.low}
                return None

        if self.allow_short and kbar.high >= self._or.or_high:
            if self.wait_bars == 0:
                stop = close + self.sl_atr * atr
                return self._signal(SignalDirection.SELL, close, stop)
            else:
                self._pending_short = {"touch_high": kbar.high}
                return None

        return None

    def _signal(self, direction: SignalDirection, price: float, stop: float) -> Signal:
        self._trades_today += 1
        tp = round(self._or.or_mid, 1) if self._or.locked else 0
        return Signal(
            direction=direction, strength=1.0,
            stop_loss=round(stop, 1), take_profit=tp,
            reason=f"or_fade {direction.value} or_mid={tp}",
            source=self.name,
        )

    def check_exit(self, position, snapshot) -> Optional[Signal]:
        if position.is_flat:
            return None
        ts = snapshot.timestamp                # 真實 bar 時間（不讀 self._current_bar_time）
        price = snapshot.price
        # 注意：check_exit 不 update _or（OR-mid 鎖後固定）、不增量 _session_bar
        is_long = (position.side == Side.LONG)

        def close_sig(reason: str) -> Signal:
            return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                          stop_loss=0, take_profit=0, reason=reason, source=self.name)

        # 1) 盤末強平（不設 cooldown，當日已到尾聲）
        if ts.time() >= self._force_close:
            return close_sig(f"盤末強平 @ {price:.0f}")

        # 2) ATR 凍結停損 + cooldown
        if position.stop_loss > 0:
            if is_long and price <= position.stop_loss:
                self._cooldown_until_bar = self._session_bar + self.cooldown
                return close_sig(f"停損 @ {price:.0f}")
            if (not is_long) and price >= position.stop_loss:
                self._cooldown_until_bar = self._session_bar + self.cooldown
                return close_sig(f"停損 @ {price:.0f}")

        # 3) OR-mid 停利（OR-mid 鎖後固定，不再更新）
        if self._or.locked:
            mid = self._or.or_mid
            if is_long and price >= mid:
                return close_sig(f"回到OR-mid停利 @ {price:.0f}")
            if (not is_long) and price <= mid:
                return close_sig(f"回到OR-mid停利 @ {price:.0f}")

        # 4) 時間停損
        if position.bars_since_entry > self.max_bars:
            return close_sig(f"時間停損 {position.bars_since_entry}根")

        return None

    def get_parameters(self) -> dict:
        return {
            "or_bars": self.or_bars, "vol_ratio_max": self.vol_ratio_max,
            "wait_bars": self.wait_bars, "sl_atr": self.sl_atr,
            "max_bars": self.max_bars, "cooldown": self.cooldown,
            "max_trades": self.max_trades, "allow_short": self.allow_short,
        }

    def reset(self) -> None:
        """引擎不會跨日呼叫；供手動測試/重啟用"""
        self._or = _OrSession(or_bars=self.or_bars)
        self._trades_today = 0
        self._cooldown_until_bar = -1
        self._session_bar = 0
        self._day = None
        self._pending_long = None
        self._pending_short = None
