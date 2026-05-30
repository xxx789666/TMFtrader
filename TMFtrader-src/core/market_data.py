"""
TMFtrader 行情資料管理
Tick → K棒轉換 + 技術指標計算（全部用 numpy/pandas，零外部 TA 庫依賴）
"""

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Callable, Optional
import numpy as np
import pandas as pd


# ============================================================
# 資料結構
# ============================================================

@dataclass
class Tick:
    """即時 Tick 資料"""
    datetime: datetime
    price: float
    volume: int
    bid_price: float = 0.0
    ask_price: float = 0.0
    instrument: str = ""  # 商品代碼（如 TMF, TGF）


@dataclass
class KBar:
    """K 棒資料"""
    datetime: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    interval: int = 1  # 分鐘


@dataclass
class MarketSnapshot:
    """市場快照 — 所有指標的當前值"""
    price: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)

    # 均線
    ema5: float = 0.0
    ema10: float = 0.0
    ema20: float = 0.0
    ema60: float = 0.0
    ema200: float = 0.0

    # 動量
    rsi: float = 50.0
    rsi_ma5: float = 50.0
    rsi_ma10: float = 50.0

    # 波動率
    atr: float = 0.0
    atr_ma20: float = 0.0
    atr_ratio: float = 1.0  # 當前 ATR / 平均 ATR

    # 趨勢強度
    adx: float = 0.0
    plus_di: float = 0.0
    minus_di: float = 0.0

    # 布林通道
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0

    # 成交量
    volume: int = 0
    volume_ma20: float = 0.0
    volume_ratio: float = 1.0

    # 近期高低點
    recent_high: float = 0.0
    recent_low: float = 999999.0

    # K 棒型態（左側交易用）
    candle_body_ratio: float = 0.0      # 實體佔比 (body / total range)
    candle_lower_shadow: float = 0.0    # 下影線長度（點數）
    candle_upper_shadow: float = 0.0    # 上影線長度（點數）
    candle_is_bullish: bool = False     # 紅K（收盤>開盤）
    candle_long_lower: bool = False     # 長下影（下影 > 實體 * 2）
    candle_long_upper: bool = False     # 長上影（上影 > 實體 * 2）
    candle_engulfing: int = 0           # 吞噬型態：+1 多頭吞噬, -1 空頭吞噬, 0 無
    volume_ma5: float = 0.0            # 5根均量
    volume_spike: bool = False          # 放量（當根 > 5根均量 * 1.5）

    # K 棒數量（策略判斷用）
    bar_count: int = 0


# ============================================================
# Tick → K 棒聚合器
# ============================================================

from loguru import logger as _agg_logger

class TickAggregator:
    """將即時 Tick 聚合成多週期 K 棒"""

    # 微台日盤 / 夜盤時段
    DAY_SESSION_START = time(8, 45)
    DAY_SESSION_END = time(13, 45)
    NIGHT_SESSION_START = time(15, 0)
    NIGHT_SESSION_END = time(5, 0)  # 隔日凌晨

    def __init__(self, intervals: list[int] = None, use_wall_clock: bool = False):
        """
        intervals: 要建構的 K 棒週期（分鐘），預設 [1, 5, 15]
        use_wall_clock: True = live/paper 模式，tick.datetime 若超過 2 分鐘落後於系統時間，
                        自動改用 datetime.now() 計算 bar_time，避免 Shioaji 回傳
                        「最後成交時間」而非現時時間導致 K 棒凍結。
        """
        self.intervals = intervals or [1, 5, 15]
        self._current_bars: dict[int, Optional[KBar]] = {i: None for i in self.intervals}
        self._completed_bars: dict[int, list[KBar]] = {i: [] for i in self.intervals}
        self._callbacks: dict[int, list[Callable]] = {i: [] for i in self.intervals}
        self.current_price: float = 0.0
        self.tick_count: int = 0
        self._diag_tick_count: int = 0  # 診斷用計數器
        self.use_wall_clock: bool = use_wall_clock
        # tick 錄製(env RECORD_TICKS=1 才啟用;預設關閉、零影響)
        try:
            from core.tick_recorder import TickRecorder
            self._tick_recorder = TickRecorder()
        except Exception:
            self._tick_recorder = None

    def on_kbar_complete(self, interval: int, callback: Callable[[KBar], None]):
        """註冊 K 棒完成回調"""
        if interval in self._callbacks:
            self._callbacks[interval].append(callback)

    def on_tick(self, tick: Tick):
        """收到 Tick，更新所有週期的 K 棒"""
        self.current_price = tick.price
        self.tick_count += 1
        self._diag_tick_count += 1

        # tick 落地(env 開關;失敗不影響主路徑)
        if self._tick_recorder is not None:
            self._tick_recorder.record(tick)

        # 診斷：每 300 個 tick 記錄一次 tick.datetime（用於排查 bar 不翻滾問題）
        if self._diag_tick_count % 300 == 1:
            cur = self._current_bars.get(1)
            cur_dt = cur.datetime if cur else None
            _agg_logger.debug(
                f"[TickDiag] tick#{self._diag_tick_count} "
                f"tick.dt={tick.datetime!r} type={type(tick.datetime).__name__} "
                f"cur_bar={cur_dt!r} price={tick.price}"
            )

        for interval in self.intervals:
            self._update_bar(tick, interval)

    def _update_bar(self, tick: Tick, interval: int):
        """更新指定週期的 K 棒"""
        bar_time = self._get_bar_time(tick.datetime, interval)
        current = self._current_bars[interval]

        if current is None or current.datetime != bar_time:
            # 前一根 K 棒完成
            if current is not None:
                self._completed_bars[interval].append(current)
                for cb in self._callbacks[interval]:
                    cb(current)

            # 開始新 K 棒
            self._current_bars[interval] = KBar(
                datetime=bar_time,
                open=tick.price,
                high=tick.price,
                low=tick.price,
                close=tick.price,
                volume=tick.volume,
                interval=interval,
            )
        else:
            # 更新當前 K 棒
            current.high = max(current.high, tick.price)
            current.low = min(current.low, tick.price)
            current.close = tick.price
            current.volume += tick.volume

    def _get_bar_time(self, dt, interval: int) -> datetime:
        """計算 K 棒所屬的時間戳（取整到 interval 分鐘）"""
        # 安全轉換：pandas Timestamp / int / float → datetime
        if hasattr(dt, 'to_pydatetime'):
            dt = dt.to_pydatetime()
        elif isinstance(dt, (int, float)):
            dt = datetime.fromtimestamp(dt if dt < 1e12 else dt / 1e9)
        # Live/paper 模式：Shioaji tick.datetime 是「最後成交時間」而非現在時間，
        # 實測落後牆上時鐘約 85 秒。門檻設為 60 秒（< 85s），讓盤中真實 tick 也
        # 能用牆上時鐘計算 bar_time，確保每分鐘正常翻滾 K 棒。
        # （舊門檻 120s：85s < 120s → 不觸發 → 使用舊 tick.dt → bar 永遠停在同一分鐘）
        if self.use_wall_clock:
            now = datetime.now()
            try:
                if (now - dt).total_seconds() > 60:
                    dt = now
            except TypeError:
                dt = now
        minutes = dt.hour * 60 + dt.minute
        bar_minutes = (minutes // interval) * interval
        return dt.replace(
            hour=bar_minutes // 60,
            minute=bar_minutes % 60,
            second=0,
            microsecond=0,
        )

    def get_bars(self, interval: int, count: int = 100) -> list[KBar]:
        """取得最近 N 根已完成的 K 棒"""
        bars = self._completed_bars.get(interval, [])
        return bars[-count:]

    def get_current_bar(self, interval: int) -> Optional[KBar]:
        """取得當前未完成的 K 棒"""
        return self._current_bars.get(interval)

    def get_bars_dataframe(self, interval: int, count: int = 200) -> pd.DataFrame:
        """取得 K 棒 DataFrame（供指標計算用）"""
        bars = self.get_bars(interval, count)
        if not bars:
            return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])

        return pd.DataFrame([
            {
                "datetime": b.datetime,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ])


# ============================================================
# 技術指標引擎（純 numpy/pandas 實作）
# ============================================================

class IndicatorEngine:
    """計算所有技術指標，每根 K 棒更新一次"""

    def __init__(self, lookback_period: int = 200):
        self.lookback = lookback_period
        self._snapshot = MarketSnapshot()
        self._bar_count = 0

    def update(self, df: pd.DataFrame) -> MarketSnapshot:
        """輸入 K 棒 DataFrame，計算所有指標並回傳快照"""
        if len(df) < 2:
            return self._snapshot

        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        volume = df["volume"].values.astype(float)

        self._bar_count += 1
        snap = MarketSnapshot()
        snap.price = close[-1]
        snap.timestamp = df["datetime"].iloc[-1]
        snap.bar_count = self._bar_count

        # 均線
        snap.ema5 = self._ema(close, 5)
        snap.ema10 = self._ema(close, 10)
        snap.ema20 = self._ema(close, 20)
        snap.ema60 = self._ema(close, 60) if len(close) >= 60 else snap.ema20
        snap.ema200 = self._ema(close, 200) if len(close) >= 200 else 0.0

        # RSI
        rsi_values = self._rsi_series(close, 14)
        snap.rsi = rsi_values[-1] if len(rsi_values) > 0 else 50.0
        if len(rsi_values) >= 10:
            snap.rsi_ma5 = float(np.mean(rsi_values[-5:]))
            snap.rsi_ma10 = float(np.mean(rsi_values[-10:]))
        else:
            snap.rsi_ma5 = snap.rsi
            snap.rsi_ma10 = snap.rsi

        # ATR
        atr_values = self._atr_series(high, low, close, 14)
        snap.atr = atr_values[-1] if len(atr_values) > 0 else 0.0
        if len(atr_values) >= 20:
            snap.atr_ma20 = float(np.mean(atr_values[-20:]))
            snap.atr_ratio = snap.atr / snap.atr_ma20 if snap.atr_ma20 > 0 else 1.0
        else:
            snap.atr_ma20 = snap.atr
            snap.atr_ratio = 1.0

        # ADX
        adx_val, plus_di, minus_di = self._adx(high, low, close, 14)
        snap.adx = adx_val
        snap.plus_di = plus_di
        snap.minus_di = minus_di

        # NaN/Inf 防護 — 指標異常時回到安全預設值
        import math
        if math.isnan(snap.atr) or math.isinf(snap.atr) or snap.atr < 0:
            snap.atr = 0.0
        if math.isnan(snap.rsi) or math.isinf(snap.rsi):
            snap.rsi = 50.0
        if math.isnan(snap.adx) or math.isinf(snap.adx):
            snap.adx = 0.0
        if math.isnan(snap.atr_ratio) or math.isinf(snap.atr_ratio):
            snap.atr_ratio = 1.0

        # 布林通道
        snap.bb_upper, snap.bb_middle, snap.bb_lower = self._bollinger(close, 20, 2.0)

        # 成交量
        snap.volume = int(volume[-1])
        if len(volume) >= 20:
            snap.volume_ma20 = float(np.mean(volume[-20:]))
            snap.volume_ratio = snap.volume / snap.volume_ma20 if snap.volume_ma20 > 0 else 1.0
        else:
            snap.volume_ma20 = float(np.mean(volume))
            snap.volume_ratio = 1.0

        # 近期高低點（20 根 K 棒）
        lookback = min(20, len(high))
        snap.recent_high = float(np.max(high[-lookback:]))
        snap.recent_low = float(np.min(low[-lookback:]))

        # K 線型態計算
        o = df["open"].values.astype(float)
        body = abs(close[-1] - o[-1])
        total_range = high[-1] - low[-1]
        snap.candle_body_ratio = body / total_range if total_range > 0 else 0.0
        snap.candle_is_bullish = close[-1] > o[-1]
        snap.candle_lower_shadow = min(o[-1], close[-1]) - low[-1]
        snap.candle_upper_shadow = high[-1] - max(o[-1], close[-1])
        snap.candle_long_lower = snap.candle_lower_shadow > body * 2 if body > 0 else False
        snap.candle_long_upper = snap.candle_upper_shadow > body * 2 if body > 0 else False

        # 吞噬型態：當根實體完全包住前根實體
        if len(close) >= 2:
            prev_body_high = max(o[-2], close[-2])
            prev_body_low = min(o[-2], close[-2])
            curr_body_high = max(o[-1], close[-1])
            curr_body_low = min(o[-1], close[-1])
            if curr_body_high > prev_body_high and curr_body_low < prev_body_low:
                snap.candle_engulfing = 1 if snap.candle_is_bullish else -1

        # 5 根均量 + 放量判斷
        vol5 = min(5, len(volume))
        snap.volume_ma5 = float(np.mean(volume[-vol5:]))
        snap.volume_spike = snap.volume > snap.volume_ma5 * 1.5 if snap.volume_ma5 > 0 else False

        self._snapshot = snap
        return snap

    def get_snapshot(self) -> MarketSnapshot:
        """取得最新快照"""
        return self._snapshot

    # ---- 指標計算函數 ----

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> float:
        """指數移動平均（最新值）"""
        if len(data) < period:
            return float(np.mean(data))
        alpha = 2.0 / (period + 1)
        ema = data[0]
        for price in data[1:]:
            ema = alpha * price + (1 - alpha) * ema
        return float(ema)

    @staticmethod
    def _ema_series(data: np.ndarray, period: int) -> np.ndarray:
        """指數移動平均（完整序列）"""
        if len(data) < 2:
            return data.copy()
        alpha = 2.0 / (period + 1)
        result = np.empty_like(data, dtype=float)
        result[0] = data[0]
        for i in range(1, len(data)):
            result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
        return result

    @staticmethod
    def _rsi_series(close: np.ndarray, period: int = 14) -> np.ndarray:
        """RSI 序列"""
        if len(close) < period + 1:
            return np.array([50.0])

        deltas = np.diff(close)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        rsi_values = []
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

            if avg_loss == 0:
                rsi_values.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

        return np.array(rsi_values) if rsi_values else np.array([50.0])

    @staticmethod
    def _atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
        """ATR 序列"""
        if len(high) < 2:
            return np.array([0.0])

        # True Range
        tr = np.empty(len(high))
        tr[0] = high[0] - low[0]
        for i in range(1, len(high)):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )

        # ATR = EMA of True Range
        if len(tr) < period:
            return np.array([float(np.mean(tr))])

        atr = np.empty(len(tr))
        atr[:period] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

        return atr[period - 1:]

    @staticmethod
    def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> tuple[float, float, float]:
        """ADX + DI+/DI-（回傳最新值）"""
        n = len(high)
        if n < period + 1:
            return 0.0, 0.0, 0.0

        # +DM / -DM
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        tr = np.zeros(n)

        for i in range(1, n):
            up = high[i] - high[i - 1]
            down = low[i - 1] - low[i]

            plus_dm[i] = up if (up > down and up > 0) else 0.0
            minus_dm[i] = down if (down > up and down > 0) else 0.0

            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )

        # 平滑
        smoothed_tr = np.mean(tr[1:period + 1]) * period
        smoothed_plus = np.mean(plus_dm[1:period + 1]) * period
        smoothed_minus = np.mean(minus_dm[1:period + 1]) * period

        dx_values = []
        for i in range(period + 1, n):
            smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr[i]
            smoothed_plus = smoothed_plus - (smoothed_plus / period) + plus_dm[i]
            smoothed_minus = smoothed_minus - (smoothed_minus / period) + minus_dm[i]

            if smoothed_tr == 0:
                continue

            plus_di = 100.0 * smoothed_plus / smoothed_tr
            minus_di = 100.0 * smoothed_minus / smoothed_tr

            di_sum = plus_di + minus_di
            if di_sum == 0:
                dx_values.append(0.0)
            else:
                dx_values.append(100.0 * abs(plus_di - minus_di) / di_sum)

        if not dx_values:
            return 0.0, 0.0, 0.0

        # ADX = EMA of DX
        adx = dx_values[0]
        for dx in dx_values[1:]:
            adx = (adx * (period - 1) + dx) / period

        # 最終 DI 值
        if smoothed_tr > 0:
            final_plus_di = 100.0 * smoothed_plus / smoothed_tr
            final_minus_di = 100.0 * smoothed_minus / smoothed_tr
        else:
            final_plus_di = 0.0
            final_minus_di = 0.0

        return float(adx), float(final_plus_di), float(final_minus_di)

    @staticmethod
    def _bollinger(close: np.ndarray, period: int = 20, std_dev: float = 2.0) -> tuple[float, float, float]:
        """布林通道（回傳最新的 upper, middle, lower）"""
        if len(close) < period:
            mean = float(np.mean(close))
            std = float(np.std(close)) if len(close) > 1 else 0.0
            return mean + std_dev * std, mean, mean - std_dev * std

        recent = close[-period:]
        middle = float(np.mean(recent))
        std = float(np.std(recent))
        return middle + std_dev * std, middle, middle - std_dev * std
