"""
Connors RSI(2) 策略：日內極短期 RSI 均值回歸。
  - _RsiState           : Wilder smoothed RSI（連續，無跨日 reset）
  - ConnorsRsi2Strategy : 進出場（Task P2b/P2c，待後續任務新增）
"""

from datetime import datetime, time
from typing import Optional

from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side


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


# ── ConnorsRsi2Strategy ──────────────────────────────────────────────────────

class ConnorsRsi2Strategy(BaseStrategy):
    def __init__(
        self,
        rsi_period: int = 2,
        rsi_low: float = 10.0,
        rsi_high: float = 90.0,
        sl_atr: float = 2.0,
        max_bars: int = 24,
        cooldown: int = 3,
        max_trades: int = 6,
        entry_window: tuple = ("09:00", "13:00"),
        force_close: str = "13:25",
        allow_short: bool = False,
        point_value: float = 10.0,
    ):
        self.rsi_period = rsi_period
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
        self.sl_atr = sl_atr
        self.max_bars = max_bars
        self.cooldown = cooldown
        self.max_trades = max_trades
        self.allow_short = allow_short
        self.point_value = point_value
        self._ew_start = time.fromisoformat(entry_window[0])
        self._ew_end   = time.fromisoformat(entry_window[1])
        self._force_close = time.fromisoformat(force_close)

        self._rsi = _RsiState(period=rsi_period)   # 跨日連續
        self._trades_today = 0
        self._cooldown_until_bar = -1
        self._session_bar = 0
        self._day = None

    @property
    def name(self) -> str:
        return "connors_rsi2"

    def _maybe_daily_reset(self, dt: datetime) -> None:
        d = dt.date()
        if d != self._day:
            self._day = d
            self._trades_today = 0
            self._cooldown_until_bar = -1
            self._session_bar = 0
            # 注意：_rsi 不 reset（連續 Wilder）

    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot) -> Optional[Signal]:
        ts = kbar.datetime
        self._maybe_daily_reset(ts)
        self._rsi.update(kbar.close)
        self._session_bar += 1
        # 守衛
        if not (self._ew_start <= ts.time() < self._ew_end):
            return None
        if self._trades_today >= self.max_trades:
            return None
        if self._session_bar <= self._cooldown_until_bar:
            return None
        rsi = self._rsi.rsi
        atr = max(snapshot.atr, 1.0)
        close = kbar.close
        if rsi <= self.rsi_low:
            stop = close - self.sl_atr * atr
            return self._signal(SignalDirection.BUY, close, stop, rsi)
        if self.allow_short and rsi >= self.rsi_high:
            stop = close + self.sl_atr * atr
            return self._signal(SignalDirection.SELL, close, stop, rsi)
        return None

    def _signal(self, direction: SignalDirection, price: float, stop: float, rsi: float) -> Signal:
        self._trades_today += 1
        return Signal(
            direction=direction, strength=1.0,
            stop_loss=round(stop, 1), take_profit=0,
            reason=f"connors_rsi2 {direction.value} rsi={rsi:.1f}",
            source=self.name,
        )

    def check_exit(self, position, snapshot) -> Optional[Signal]:
        if position.is_flat:
            return None
        ts = snapshot.timestamp          # 真實 bar 時間
        price = snapshot.price
        self._rsi.update(price)          # 持倉期間維持 RSI（close-proxy）
        # 注意：_session_bar 在 check_exit **不**增量（只 on_kbar flat bar 才算）
        is_long = (position.side == Side.LONG)

        def close_sig(reason):
            return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                          stop_loss=0, take_profit=0, reason=reason, source=self.name)

        # 1) 盤末強平
        if ts.time() >= self._force_close:
            return close_sig(f"盤末強平 @ {price:.0f}")
        # 2) ATR 凍結停損 + cooldown 接線
        if position.stop_loss > 0:
            if is_long and price <= position.stop_loss:
                self._cooldown_until_bar = self._session_bar + self.cooldown
                return close_sig(f"停損 @ {price:.0f}")
            if (not is_long) and price >= position.stop_loss:
                self._cooldown_until_bar = self._session_bar + self.cooldown
                return close_sig(f"停損 @ {price:.0f}")
        # 3) RSI 回中
        rsi = self._rsi.rsi
        if is_long and rsi >= 50:
            return close_sig(f"RSI回中停利 rsi={rsi:.1f}")
        if (not is_long) and rsi <= 50:
            return close_sig(f"RSI回中停利 rsi={rsi:.1f}")
        # 4) 時間停損
        if position.bars_since_entry > self.max_bars:
            return close_sig(f"時間停損 {position.bars_since_entry}根")
        return None

    def get_parameters(self):
        return {
            "rsi_period": self.rsi_period, "rsi_low": self.rsi_low, "rsi_high": self.rsi_high,
            "sl_atr": self.sl_atr, "max_bars": self.max_bars,
            "max_trades": self.max_trades, "cooldown": self.cooldown,
            "allow_short": self.allow_short,
        }

    def reset(self):
        # 引擎不會跨日呼叫此方法（vwap_fade plan §1 表確認），這裡只供手動測試/重啟用
        self._rsi = _RsiState(period=self.rsi_period)
        self._trades_today = 0
        self._cooldown_until_bar = -1
        self._session_bar = 0
        self._day = None
