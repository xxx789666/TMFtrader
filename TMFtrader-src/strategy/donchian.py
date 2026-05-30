"""
Donchian Breakout 策略（vwap_fade §9 C1 後第一支 trend-following）。
  - _DonchianState   : N+K+1 根滑動視窗、entry 不含當前 bar、exit 含當前 bar
  - DonchianStrategy : 結構性突破進場 + opposite K-bar trailing 出場（Task P2b/P2c，待後續任務）
"""

from collections import deque
from datetime import datetime, time
from typing import Optional
import math

from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side


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

class DonchianStrategy(BaseStrategy):
    def __init__(
        self,
        entry_n: int = 20,
        exit_k: int = 10,
        sl_atr: float = 2.0,
        max_bars: int = 48,
        cooldown: int = 3,
        max_trades: int = 5,
        entry_window_end: str = "12:00",
        force_close: str = "13:25",
        allow_short: bool = False,
        point_value: float = 10.0,
        vol_mult: float = 0.0,
    ):
        self.entry_n = entry_n
        self.exit_k = exit_k
        self.sl_atr = sl_atr
        self.max_bars = max_bars
        self.cooldown = cooldown
        self.max_trades = max_trades
        self.allow_short = allow_short
        self.point_value = point_value
        # v2 量能確認濾網：進場突破 bar 要求 volume_ratio >= vol_mult。
        # 0.0 = off（向後相容 v1）；選 volume 而非 ATR/EMA 以維持與 BreakoutTrend 差異化。
        self.vol_mult = vol_mult

        # _ew_start 動態 = 08:45 + entry_n*5min
        ew_start_min = 8 * 60 + 45 + entry_n * 5
        self._ew_start = time(ew_start_min // 60, ew_start_min % 60)
        self._ew_end = time.fromisoformat(entry_window_end)
        self._force_close = time.fromisoformat(force_close)

        self._donchian = _DonchianState(entry_n=entry_n, exit_k=exit_k)
        self._trades_today = 0
        self._cooldown_until_bar = -1
        self._session_bar = 0
        self._day = None

    @property
    def name(self) -> str:
        return "donchian"

    def _maybe_daily_reset(self, dt: datetime) -> None:
        d = dt.date()
        if d != self._day:
            self._day = d
            self._trades_today = 0
            self._cooldown_until_bar = -1
            self._session_bar = 0
            # _donchian 由 update() 內 _maybe_reset 自動清空

    def on_kbar(self, kbar, snapshot):
        ts = kbar.datetime
        # ── 鐵律順序：reset → update → session_bar → guards → entry ──────
        self._maybe_daily_reset(ts)
        self._donchian.update(ts, kbar.high, kbar.low)
        self._session_bar += 1

        # 守衛
        if not self._donchian.warmup_ready:
            return None
        if not (self._ew_start <= ts.time() < self._ew_end):
            return None
        if self._trades_today >= self.max_trades:
            return None
        if self._session_bar <= self._cooldown_until_bar:
            return None

        # 進場（entry_n_high/low 不含當前 bar）
        atr = max(snapshot.atr, 1.0)
        close = kbar.close
        # v2 量能確認：突破 bar 的量能須達 vol_mult 倍均量（0.0=off）。
        # volume_ratio 由引擎以 trailing 20根均量因果計算、無 lookahead。
        vol_ok = snapshot.volume_ratio >= self.vol_mult
        if vol_ok and close > self._donchian.entry_n_high:
            stop = close - self.sl_atr * atr
            return self._signal(SignalDirection.BUY, close, stop)
        if vol_ok and self.allow_short and close < self._donchian.entry_n_low:
            stop = close + self.sl_atr * atr
            return self._signal(SignalDirection.SELL, close, stop)
        return None

    def _signal(self, direction, price, stop):
        self._trades_today += 1
        return Signal(
            direction=direction, strength=1.0,
            stop_loss=round(stop, 1), take_profit=0,
            reason=f"donchian {direction.value} N={self.entry_n}",
            source=self.name,
        )

    def check_exit(self, position, snapshot):
        if position.is_flat:
            return None
        ts = snapshot.timestamp                # 真實 bar 時間
        price = snapshot.price
        # 注意：不 update _donchian（state 已由 on_kbar update 完）、不 increment _session_bar
        is_long = (position.side == Side.LONG)

        def close_sig(reason):
            return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                          stop_loss=0, take_profit=0, reason=reason, source=self.name)

        # 1) 盤末強平（不設 cooldown）
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
        # 3) Donchian K-bar trailing exit
        if is_long:
            ek_low = self._donchian.exit_k_low
            if not math.isnan(ek_low) and price < ek_low:
                return close_sig(f"Donchian K-low trail @ {price:.0f}")
        else:
            ek_high = self._donchian.exit_k_high
            if not math.isnan(ek_high) and price > ek_high:
                return close_sig(f"Donchian K-high trail @ {price:.0f}")
        # 4) 時間停損
        if position.bars_since_entry > self.max_bars:
            return close_sig(f"時間停損 {position.bars_since_entry}根")
        return None

    def get_parameters(self):
        return {
            "entry_n": self.entry_n, "exit_k": self.exit_k, "sl_atr": self.sl_atr,
            "max_bars": self.max_bars, "cooldown": self.cooldown,
            "max_trades": self.max_trades, "allow_short": self.allow_short,
            "vol_mult": self.vol_mult,
        }

    def reset(self):
        """引擎不會跨日呼叫；供手動測試/重啟用"""
        self._donchian = _DonchianState(entry_n=self.entry_n, exit_k=self.exit_k)
        self._trades_today = 0
        self._cooldown_until_bar = -1
        self._session_bar = 0
        self._day = None
