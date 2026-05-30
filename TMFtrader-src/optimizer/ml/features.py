"""Stage 2: Feature engineering for the ML breakout pipeline.

For each 5-min bar in each instrument's dataset, compute 200+ features using
the precomputed indicators from core.gpu_indicators.precompute_all().

Features are saved as {instrument}_features.parquet in data/historical/ml/.

Usage:
    python optimizer/ml/features.py
    python optimizer/ml/features.py --data-dir data/historical/ml
"""

import sys
import argparse
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

import numpy as np
import pandas as pd
from core.gpu_indicators import precompute_all

ROOT = Path(__file__).parent.parent.parent

# ---------------------------------------------------------------------------
# Exported feature name list (populated when build_feature_matrix() is first called)
# ---------------------------------------------------------------------------
FEATURE_NAMES = []  # populated when build_feature_matrix() is first called

# ---------------------------------------------------------------------------
# Extra indicator computations (manual, for periods not in precompute_all)
# ---------------------------------------------------------------------------

def compute_rsi_arr(close: np.ndarray, period: int) -> np.ndarray:
    """Wilder-smoothed RSI for an arbitrary period (pure numpy, look-ahead free)."""
    n = len(close)
    rsi = np.full(n, 50.0, dtype=np.float64)
    if n < period + 2:
        return rsi.astype(np.float32)

    d = np.diff(close)
    gains  = np.where(d > 0,  d, 0.0)
    losses = np.where(d < 0, -d, 0.0)

    ag = np.mean(gains[:period])
    al = np.mean(losses[:period])

    for i in range(period):
        rsi[i] = 100.0 - 100.0 / (1.0 + ag / al) if al > 0 else 100.0

    for i in range(period, n - 1):
        ag = (ag * (period - 1) + gains[i])  / period
        al = (al * (period - 1) + losses[i]) / period
        rsi[i + 1] = 100.0 - 100.0 / (1.0 + ag / al) if al > 0 else 100.0

    return rsi.astype(np.float32)


def compute_adx_period(high: np.ndarray,
                        low: np.ndarray,
                        close: np.ndarray,
                        period: int) -> np.ndarray:
    """Return ADX array for the given period (Wilder smoothing, pure numpy)."""
    n = len(high)
    adx_arr  = np.zeros(n, dtype=np.float64)
    if n < period + 2:
        return adx_arr.astype(np.float32)

    tr   = np.empty(n)
    pdm  = np.empty(n)
    ndm  = np.empty(n)
    tr[0] = high[0] - low[0]
    pdm[0] = 0.0
    ndm[0] = 0.0

    for i in range(1, n):
        up   = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        pdm[i] = up   if (up > down   and up   > 0) else 0.0
        ndm[i] = down if (down > up   and down > 0) else 0.0
        a = high[i] - low[i]
        b = abs(high[i] - close[i - 1])
        c = abs(low[i]  - close[i - 1])
        tr[i] = a if a >= b and a >= c else (b if b >= c else c)

    str_ = np.sum(tr[1:period + 1])
    spdm = np.sum(pdm[1:period + 1])
    sndm = np.sum(ndm[1:period + 1])

    adx_val  = 0.0
    dx_count = 0

    for i in range(period + 1, n):
        str_ = str_ - str_ / period + tr[i]
        spdm = spdm - spdm / period + pdm[i]
        sndm = sndm - sndm / period + ndm[i]
        if str_ == 0.0:
            continue
        pdi = 100.0 * spdm / str_
        ndi = 100.0 * sndm / str_
        di_sum = pdi + ndi
        dx = 100.0 * abs(pdi - ndi) / di_sum if di_sum > 0 else 0.0
        adx_val = dx if dx_count == 0 else (adx_val * (period - 1) + dx) / period
        dx_count += 1
        adx_arr[i] = adx_val

    return adx_arr.astype(np.float32)


def compute_adx_full(high: np.ndarray,
                     low: np.ndarray,
                     close: np.ndarray,
                     period: int):
    """Return (adx, pdi, ndi) arrays for the given period (Wilder smoothing, pure numpy)."""
    n = len(high)
    adx_arr = np.zeros(n, dtype=np.float64)
    pdi_out = np.zeros(n, dtype=np.float64)
    ndi_out = np.zeros(n, dtype=np.float64)
    if n < period + 2:
        return adx_arr.astype(np.float32), pdi_out.astype(np.float32), ndi_out.astype(np.float32)

    tr  = np.empty(n)
    pdm = np.empty(n)
    ndm = np.empty(n)
    tr[0] = high[0] - low[0]
    pdm[0] = 0.0
    ndm[0] = 0.0

    for i in range(1, n):
        up   = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        pdm[i] = up   if (up > down   and up   > 0) else 0.0
        ndm[i] = down if (down > up   and down > 0) else 0.0
        a = high[i] - low[i]
        b = abs(high[i] - close[i - 1])
        c = abs(low[i]  - close[i - 1])
        tr[i] = a if a >= b and a >= c else (b if b >= c else c)

    str_ = np.sum(tr[1:period + 1])
    spdm = np.sum(pdm[1:period + 1])
    sndm = np.sum(ndm[1:period + 1])

    adx_val  = 0.0
    dx_count = 0

    for i in range(period + 1, n):
        str_ = str_ - str_ / period + tr[i]
        spdm = spdm - spdm / period + pdm[i]
        sndm = sndm - sndm / period + ndm[i]
        if str_ == 0.0:
            continue
        pdi_val = 100.0 * spdm / str_
        ndi_val = 100.0 * sndm / str_
        pdi_out[i] = pdi_val
        ndi_out[i] = ndi_val
        di_sum = pdi_val + ndi_val
        dx = 100.0 * abs(pdi_val - ndi_val) / di_sum if di_sum > 0 else 0.0
        adx_val = dx if dx_count == 0 else (adx_val * (period - 1) + dx) / period
        dx_count += 1
        adx_arr[i] = adx_val

    return adx_arr.astype(np.float32), pdi_out.astype(np.float32), ndi_out.astype(np.float32)


def compute_atr_period(high: np.ndarray,
                        low: np.ndarray,
                        close: np.ndarray,
                        period: int) -> np.ndarray:
    """Wilder ATR for given period (pure numpy)."""
    n = len(high)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        a = high[i] - low[i]
        b = abs(high[i] - close[i - 1])
        c = abs(low[i]  - close[i - 1])
        tr[i] = a if a >= b and a >= c else (b if b >= c else c)

    atr = np.empty(n)
    init = np.mean(tr[:period]) if n >= period else tr[0]
    for i in range(period):
        atr[i] = init
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr.astype(np.float32)


def compute_ema(arr: np.ndarray, period: int) -> np.ndarray:
    """EMA for given period (pure numpy)."""
    n = len(arr)
    out = np.empty(n, dtype=np.float64)
    k = 2.0 / (period + 1)
    out[0] = arr[0]
    for i in range(1, n):
        out[i] = arr[i] * k + out[i - 1] * (1.0 - k)
    return out.astype(np.float32)


def _safe_div(a: np.ndarray, b: np.ndarray, fill: float = 0.0) -> np.ndarray:
    """Element-wise a/b; replaces division-by-zero with fill."""
    with np.errstate(divide="ignore", invalid="ignore"):
        result = np.where(b != 0, a / b, fill)
    return result.astype(np.float32)


def _lag(arr: np.ndarray, k: int) -> np.ndarray:
    """Lag array by k steps (fills first k with arr[0])."""
    out = np.empty_like(arr)
    out[:k] = arr[0]
    out[k:] = arr[:-k]
    return out


def _rolling_count_below(arr: np.ndarray, threshold: float,
                          window: int) -> np.ndarray:
    """Count of values < threshold in a rolling window (pure numpy, O(n*w))."""
    n = len(arr)
    out = np.zeros(n, dtype=np.float32)
    for i in range(n):
        s = max(0, i - window + 1)
        out[i] = float(np.sum(arr[s:i + 1] < threshold))
    return out


def _rolling_count_up(close: np.ndarray, window: int) -> np.ndarray:
    """Count of bars where close > prev close in last `window` bars."""
    n = len(close)
    up = np.zeros(n, dtype=np.float32)
    for i in range(1, n):
        s = max(1, i - window + 1)
        up[i] = float(np.sum(close[s:i + 1] > close[s - 1:i]))
    return up


def _rolling_std(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling standard deviation (pure numpy via pandas)."""
    return pd.Series(arr).rolling(window, min_periods=2).std().fillna(0.0).values.astype(np.float32)


def _rolling_skew(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling skewness (pandas)."""
    return pd.Series(arr).rolling(window, min_periods=3).skew().fillna(0.0).values.astype(np.float32)


def _rolling_mean(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling mean (pandas)."""
    return pd.Series(arr).rolling(window, min_periods=1).mean().values.astype(np.float32)


def _rolling_max(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling max (pandas)."""
    return pd.Series(arr).rolling(window, min_periods=1).max().values.astype(np.float32)


def _rolling_min(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling min (pandas)."""
    return pd.Series(arr).rolling(window, min_periods=1).min().values.astype(np.float32)


def _rolling_mean_abs_dev(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling mean absolute deviation from the rolling mean (pandas)."""
    s = pd.Series(arr)
    rm = s.rolling(window, min_periods=1).mean()
    mad = s.rolling(window, min_periods=1).apply(
        lambda x: np.mean(np.abs(x - np.mean(x))), raw=True
    ).fillna(0.0)
    return mad.values.astype(np.float32)


# ---------------------------------------------------------------------------
# Session metadata helpers
# ---------------------------------------------------------------------------

def _session_meta(datetimes: pd.DatetimeIndex,
                  session_start_h: int,
                  session_start_m: int,
                  session_total_minutes: float):
    """Return (minute_in_session, bar_count_in_session) arrays (float32)."""
    n = len(datetimes)
    minute_in_session    = np.zeros(n, dtype=np.float32)
    bar_count_in_session = np.zeros(n, dtype=np.float32)

    prev_date  = None
    bar_in_day = 0

    for i, dt in enumerate(datetimes):
        d = dt.date()
        if d != prev_date:
            bar_in_day = 0
            prev_date  = d
        bar_in_day += 1
        bar_count_in_session[i] = float(bar_in_day)

        mins_since = (dt.hour * 60 + dt.minute) - (session_start_h * 60 + session_start_m)
        minute_in_session[i] = float(max(0.0, mins_since) / session_total_minutes)

    return minute_in_session, bar_count_in_session


# ---------------------------------------------------------------------------
# Multi-timeframe 15-min indicator helper
# ---------------------------------------------------------------------------

def _compute_15m_indicators(df_5m: pd.DataFrame) -> pd.DataFrame:
    """Resample 5-min OHLCV to 15-min and compute RSI14, ADX14, ATR/ATR_MA, EMA20 slope.

    Returns a DataFrame with columns:
        datetime_15m, rsi14_15m, adx14_15m, atr_ratio_15m, ema20_slope_15m, rsi_vs50_15m
    All values are float32.
    """
    df = df_5m.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime")

    # Resample to 15-min bars
    df_15 = df.resample("15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["close"])

    if len(df_15) < 30:
        # Not enough data; return empty placeholder
        empty = pd.DataFrame(columns=[
            "datetime_15m", "rsi14_15m", "adx14_15m",
            "atr_ratio_15m", "ema20_slope_15m", "rsi_vs50_15m"
        ])
        return empty

    c15 = df_15["close"].values.astype(np.float64)
    h15 = df_15["high"].values.astype(np.float64)
    l15 = df_15["low"].values.astype(np.float64)

    rsi14_15 = compute_rsi_arr(c15, 14)
    adx14_15 = compute_adx_period(h15, l15, c15, 14)
    atr14_15 = compute_atr_period(h15, l15, c15, 14)
    atr_ma20_15 = _rolling_mean(atr14_15, 20)
    atr_ratio_15 = _safe_div(atr14_15, atr_ma20_15)

    ema20_15 = compute_ema(c15, 20)
    ema20_15_lag1 = _lag(ema20_15, 1)
    ema20_slope_15 = _safe_div(
        (ema20_15 - ema20_15_lag1).astype(np.float32),
        np.where(atr14_15 > 0, atr14_15, np.ones_like(atr14_15)).astype(np.float32)
    )

    rsi_vs50_15 = (rsi14_15 - 50.0).astype(np.float32)

    result_15 = pd.DataFrame({
        "datetime_15m":    df_15.index,
        "rsi14_15m":       rsi14_15,
        "adx14_15m":       adx14_15,
        "atr_ratio_15m":   atr_ratio_15,
        "ema20_slope_15m": ema20_slope_15,
        "rsi_vs50_15m":    rsi_vs50_15,
    }).reset_index(drop=True)

    return result_15


# ---------------------------------------------------------------------------
# Core feature builder
# ---------------------------------------------------------------------------

def build_feature_matrix(df_5m: pd.DataFrame,
                          ind: dict,
                          instrument: str) -> pd.DataFrame:
    """Compute all features for a single instrument's 5-min bar DataFrame.

    Args:
        df_5m:      5-min OHLCV DataFrame with a 'datetime' column.
        ind:        Dict of np.ndarrays from precompute_all().
        instrument: Instrument name string (stored as a column).

    Returns:
        DataFrame with columns: datetime, instrument, + all f_* features (200+).
    """
    global FEATURE_NAMES

    n = len(df_5m)
    datetimes = pd.DatetimeIndex(pd.to_datetime(df_5m["datetime"]))

    close  = ind["close"].astype(np.float64)
    high   = ind["high"].astype(np.float64)
    low    = ind["low"].astype(np.float64)
    open_  = ind["open"].astype(np.float64)
    volume = ind["volume"].astype(np.float64)

    atr14     = ind["atr"].astype(np.float32)
    atr_ma20  = ind["atr_ma20"].astype(np.float32)
    atr_ratio = ind["atr_ratio"].astype(np.float32)
    rsi14     = ind["rsi"].astype(np.float32)
    rsi_ma5   = ind["rsi_ma5"].astype(np.float32)
    adx_arr   = ind["adx"].astype(np.float32)
    pdi_arr   = ind["plus_di"].astype(np.float32)
    ndi_arr   = ind["minus_di"].astype(np.float32)
    ema5      = ind["ema5"].astype(np.float32)
    ema20     = ind["ema20"].astype(np.float32)
    ema60     = ind["ema60"].astype(np.float32)
    ema200    = ind["ema200"].astype(np.float32)
    bb_upper  = ind["bb_upper"].astype(np.float32)
    bb_lower  = ind["bb_lower"].astype(np.float32)
    bb_mid    = ind["bb_middle"].astype(np.float32)
    vol_ratio = ind["volume_ratio"].astype(np.float32)

    close32 = close.astype(np.float32)
    high32  = high.astype(np.float32)
    low32   = low.astype(np.float32)
    open32  = open_.astype(np.float32)

    # ==========================================================================
    # Group 1 — ATR / Squeeze state (15 original features)
    # ==========================================================================
    f_atr_ratio      = atr_ratio
    f_atr_ratio_lag1 = _lag(atr_ratio, 1)
    f_atr_ratio_lag2 = _lag(atr_ratio, 2)

    ar_lag5 = _lag(atr_ratio, 5)
    f_atr_ratio_slope = ((atr_ratio - ar_lag5) / 5.0).astype(np.float32)

    f_atr_pct = _safe_div(atr14, close32)

    bb_range = bb_upper - bb_lower
    f_bb_width = _safe_div(bb_range, np.where(bb_mid != 0, bb_mid,
                                               np.ones_like(bb_mid)).astype(np.float32))

    # ATR vs 7-day context (rolling mean over 20*7 = 140 bars)
    atr_long_ma = pd.Series(atr14).rolling(140, min_periods=1).mean().values.astype(np.float32)
    f_atr_vs_7d = _safe_div(atr14, atr_long_ma)

    # Squeeze duration: count of last 20 bars where atr_ratio < 0.9
    f_squeeze_dur = _rolling_count_below(atr_ratio, 0.9, 20)

    f_vol_ratio      = vol_ratio
    f_vol_ratio_lag1 = _lag(vol_ratio, 1)
    f_vol_spike      = ind["volume_spike"].astype(np.float32)

    f_body_ratio = ind["candle_body_ratio"].astype(np.float32)

    upper_shadow = ind["candle_upper_shadow"].astype(np.float32)
    lower_shadow = ind["candle_lower_shadow"].astype(np.float32)
    f_upper_wick_atr = _safe_div(upper_shadow, atr14)
    f_lower_wick_atr = _safe_div(lower_shadow, atr14)

    f_engulfing = ind["candle_engulfing"].astype(np.float32)

    # ==========================================================================
    # Group 2 — Trend / ADX (15 original features)
    # ==========================================================================
    f_adx      = adx_arr
    f_adx_lag5 = _lag(adx_arr, 5)
    f_adx_slope = ((adx_arr - f_adx_lag5) / 5.0).astype(np.float32)

    f_pdi = pdi_arr
    f_ndi = ndi_arr
    f_di_spread     = (pdi_arr - ndi_arr).astype(np.float32)
    f_di_spread_abs = np.abs(f_di_spread).astype(np.float32)

    f_ema5_vs_ema20  = _safe_div((ema5  - ema20).astype(np.float32), atr14)
    f_ema20_vs_ema60 = _safe_div((ema20 - ema60).astype(np.float32), atr14)

    f_ema20_vs_ema200 = _safe_div((ema20 - ema200).astype(np.float32),
                                   close32) * 100.0
    f_close_vs_ema200 = _safe_div((close32 - ema200).astype(np.float32),
                                   close32) * 100.0
    f_close_vs_ema20  = _safe_div((close32 - ema20).astype(np.float32), atr14)

    ema5_lag5  = _lag(ema5,  5)
    ema20_lag5 = _lag(ema20, 5)
    f_ema5_slope  = _safe_div((ema5  - ema5_lag5).astype(np.float32),  atr14) / 5.0
    f_ema20_slope = _safe_div((ema20 - ema20_lag5).astype(np.float32), atr14) / 5.0

    recent_high = ind["recent_high"].astype(np.float32)
    recent_low  = ind["recent_low"].astype(np.float32)
    f_recent_high_dist = _safe_div((recent_high - close32).astype(np.float32), atr14)
    f_recent_low_dist  = _safe_div((close32 - recent_low).astype(np.float32), atr14)

    # ==========================================================================
    # Group 3 — Momentum / RSI (10 original features)
    # ==========================================================================
    f_rsi      = rsi14
    f_rsi_ma5  = rsi_ma5
    rsi_lag5   = _lag(rsi14, 5)
    f_rsi_slope = (rsi14 - rsi_lag5).astype(np.float32)
    f_rsi_vs_50 = (rsi14 - 50.0).astype(np.float32)

    close_lag1  = _lag(close32, 1)
    close_lag3  = _lag(close32, 3)
    close_lag5  = _lag(close32, 5)
    close_lag10 = _lag(close32, 10)
    close_lag20 = _lag(close32, 20)

    f_ret1  = _safe_div((close32 - close_lag1).astype(np.float32),  atr14)
    f_ret3  = _safe_div((close32 - close_lag3).astype(np.float32),  atr14)
    f_ret5  = _safe_div((close32 - close_lag5).astype(np.float32),  atr14)
    f_ret10 = _safe_div((close32 - close_lag10).astype(np.float32), atr14)
    f_ret20 = _safe_div((close32 - close_lag20).astype(np.float32), atr14)

    # Sum of last 5 bar directions (up=+1, down=-1, flat=0)
    diffs = np.sign(np.diff(close32, prepend=close32[0])).astype(np.float32)
    f_ret_vs_atr = pd.Series(diffs).rolling(5, min_periods=1).sum().values.astype(np.float32)

    # ==========================================================================
    # Group 4 — Time features (5 original features)
    # ==========================================================================
    hours   = datetimes.hour.values.astype(np.float32)
    minutes = datetimes.minute.values.astype(np.float32)

    f_hour_sin = np.sin(2 * np.pi * hours / 24.0).astype(np.float32)
    f_hour_cos = np.cos(2 * np.pi * hours / 24.0).astype(np.float32)

    # Session start depends on instrument family
    _US_INDEX_SET = {"SPXL", "TECL", "TQQQ",
                     "QQQM", "IVV", "XLK", "SOXX", "IBB", "FNGS",
                     "NAS100", "USTEC", "US500"}
    _CRYPTO_SET   = {"BTCUSDT"}  # only BTC retained; other altcoins removed
    if instrument in _CRYPTO_SET:
        sess_h, sess_m, sess_total = 8, 0, 480.0    # 08:00~16:00 TWN
    elif instrument in _US_INDEX_SET:
        sess_h, sess_m, sess_total = 14, 30, 390.0  # 14:30~21:00 UTC
    else:
        sess_h, sess_m, sess_total = 8, 45, 285.0   # 08:45~13:30 TWN (TMF/TX)

    f_minute_in_session, f_bar_count_in_session = _session_meta(
        datetimes, sess_h, sess_m, sess_total
    )

    f_day_of_week  = datetimes.dayofweek.values.astype(np.float32)
    f_is_afternoon = (hours >= 11).astype(np.float32)

    # ==========================================================================
    # Group 5 — Additional indicators (10 original features)
    # ==========================================================================
    f_rsi7  = compute_rsi_arr(close, 7)
    f_rsi21 = compute_rsi_arr(close, 21)
    f_adx7  = compute_adx_period(high, low, close, 7)

    atr7 = compute_atr_period(high, low, close, 7)
    f_atr7_ratio = _safe_div(atr7, atr_ma20)
    f_atr7_pct   = _safe_div(atr7, close32)

    bb_range_safe = np.where(bb_range > 0, bb_range, np.ones_like(bb_range)).astype(np.float32)
    f_bb_pos = _safe_div((close32 - bb_lower).astype(np.float32), bb_range_safe)

    f_close_vs_bb_mid = _safe_div((close32 - bb_mid).astype(np.float32), atr14)

    vol_ratio_lag5 = _lag(vol_ratio, 5)
    f_vol_slope = (vol_ratio - vol_ratio_lag5).astype(np.float32)

    f_consecutive_up = _rolling_count_up(close32, 5)

    # ==========================================================================
    # Group A — Multi-period RSI (7 new features)
    # ==========================================================================
    # f_rsi7 and f_rsi21 already computed above
    f_rsi50 = compute_rsi_arr(close, 50)

    rsi7_lag3  = _lag(f_rsi7, 3)
    rsi21_lag3 = _lag(f_rsi21, 3)
    f_rsi7_slope  = ((f_rsi7  - rsi7_lag3)  / 3.0).astype(np.float32)
    f_rsi21_slope = ((f_rsi21 - rsi21_lag3) / 3.0).astype(np.float32)

    f_rsi7_vs_rsi21   = (f_rsi7  - f_rsi21).astype(np.float32)
    f_rsi14_vs_rsi50  = (rsi14   - f_rsi50).astype(np.float32)

    # ==========================================================================
    # Group B — Multi-period ATR/ADX (7 new features)
    # ==========================================================================
    adx21_arr, pdi21_arr, ndi21_arr = compute_adx_full(high, low, close, 21)
    f_adx21    = adx21_arr
    f_pdi21    = pdi21_arr
    f_ndi21    = ndi21_arr
    f_di_spread21 = (f_pdi21 - f_ndi21).astype(np.float32)

    adx21_lag5 = _lag(f_adx21, 5)
    f_adx21_slope = ((f_adx21 - adx21_lag5) / 5.0).astype(np.float32)

    atr21 = compute_atr_period(high, low, close, 21)
    atr21_ma20 = _rolling_mean(atr21, 20)
    f_atr21_ratio = _safe_div(atr21, atr21_ma20)

    atr7_ratio_arr = f_atr7_ratio  # already computed above
    atr7_r_lag3 = _lag(atr7_ratio_arr, 3)
    f_atr7_slope = ((atr7_ratio_arr - atr7_r_lag3) / 3.0).astype(np.float32)

    f_atr_diff = (f_atr_ratio - f_atr21_ratio).astype(np.float32)

    # ==========================================================================
    # Group C — MACD, Stochastic, Williams, CCI (8 new features)
    # ==========================================================================
    ema12 = compute_ema(close, 12)
    ema26 = compute_ema(close, 26)
    macd_line = (ema12 - ema26).astype(np.float64)
    macd_signal_arr = compute_ema(macd_line, 9)
    macd_hist_arr = (macd_line - macd_signal_arr).astype(np.float32)
    f_macd_hist   = macd_hist_arr
    f_macd_signal = macd_signal_arr.astype(np.float32)
    macd_hist_lag3 = _lag(f_macd_hist, 3)
    f_macd_slope = ((f_macd_hist - macd_hist_lag3) / 3.0).astype(np.float32)

    high_max14 = _rolling_max(high32, 14)
    low_min14  = _rolling_min(low32,  14)
    stoch_range = (high_max14 - low_min14)
    stoch_range_safe = np.where(stoch_range > 0, stoch_range, np.ones_like(stoch_range)).astype(np.float32)
    stoch_k_raw = 100.0 * _safe_div((close32 - low_min14).astype(np.float32), stoch_range_safe)
    f_stoch_k = stoch_k_raw
    f_stoch_d = _rolling_mean(stoch_k_raw, 3)

    f_williams_r = (-100.0 * _safe_div(
        (high_max14 - close32).astype(np.float32), stoch_range_safe
    )).astype(np.float32)

    close_mean14 = _rolling_mean(close32, 14)
    close_mad14  = _rolling_mean_abs_dev(close32, 14)
    close_mad14_safe = np.where(close_mad14 > 0, close_mad14, np.ones_like(close_mad14)).astype(np.float32)
    f_cci14 = ((close32 - close_mean14) / (0.015 * close_mad14_safe)).astype(np.float32)

    close_mean20 = _rolling_mean(close32, 20)
    close_mad20  = _rolling_mean_abs_dev(close32, 20)
    close_mad20_safe = np.where(close_mad20 > 0, close_mad20, np.ones_like(close_mad20)).astype(np.float32)
    f_cci20 = ((close32 - close_mean20) / (0.015 * close_mad20_safe)).astype(np.float32)

    # ==========================================================================
    # Group D — Multi-timeframe 15-min (5 new features)
    # ==========================================================================
    df_5m_work = df_5m.copy()
    df_5m_work["datetime"] = pd.to_datetime(df_5m_work["datetime"])

    df_15m_ind = _compute_15m_indicators(df_5m_work)

    # Default arrays (all zeros) in case 15m data is insufficient
    f_rsi14_15m      = np.zeros(n, dtype=np.float32)
    f_adx14_15m      = np.zeros(n, dtype=np.float32)
    f_atr_ratio_15m  = np.zeros(n, dtype=np.float32)
    f_ema20_slope_15m = np.zeros(n, dtype=np.float32)
    f_rsi_vs50_15m   = np.zeros(n, dtype=np.float32)

    if len(df_15m_ind) > 0:
        df_5m_ts = df_5m_work[["datetime"]].copy()
        df_5m_ts = df_5m_ts.sort_values("datetime").reset_index(drop=True)

        df_15m_merge = df_15m_ind.rename(columns={"datetime_15m": "datetime"}).sort_values("datetime")

        merged = pd.merge_asof(
            df_5m_ts,
            df_15m_merge,
            on="datetime",
            direction="backward"
        )
        f_rsi14_15m       = merged["rsi14_15m"].fillna(0.0).values.astype(np.float32)
        f_adx14_15m       = merged["adx14_15m"].fillna(0.0).values.astype(np.float32)
        f_atr_ratio_15m   = merged["atr_ratio_15m"].fillna(0.0).values.astype(np.float32)
        f_ema20_slope_15m = merged["ema20_slope_15m"].fillna(0.0).values.astype(np.float32)
        f_rsi_vs50_15m    = merged["rsi_vs50_15m"].fillna(0.0).values.astype(np.float32)

    # ==========================================================================
    # Group E — Rolling return statistics (5 new features)
    # ==========================================================================
    # Raw log returns for statistical moments
    log_ret = np.log(close32 / np.where(close_lag1 > 0, close_lag1, close32))
    f_ret_std5  = _rolling_std(log_ret, 5)
    f_ret_std10 = _rolling_std(log_ret, 10)
    f_ret_std20 = _rolling_std(log_ret, 20)
    f_ret_skew10 = _rolling_skew(log_ret, 10)
    f_ret_skew20 = _rolling_skew(log_ret, 20)

    # ==========================================================================
    # Group F — Additional price structure (6 new features)
    # ==========================================================================
    f_dist_recent_high200 = ((_rolling_max(close32, 200) - close32) / np.where(close32 > 0, close32, np.ones_like(close32)) * 100.0).astype(np.float32)
    f_dist_recent_low200  = ((close32 - _rolling_min(close32, 200)) / np.where(close32 > 0, close32, np.ones_like(close32)) * 100.0).astype(np.float32)
    f_range_ratio         = _safe_div((high32 - low32).astype(np.float32), atr14)
    f_open_vs_close       = _safe_div((close32 - open32).astype(np.float32), atr14)
    f_high_vs_ema20       = _safe_div((high32  - ema20).astype(np.float32), atr14)
    f_low_vs_ema20        = _safe_div((low32   - ema20).astype(np.float32), atr14)

    # ==========================================================================
    # Group G — Volume enriched (3 new features)
    # ==========================================================================
    vol_ma50 = _rolling_mean(volume.astype(np.float32), 50)
    f_vol_ma50_ratio = _safe_div(volume.astype(np.float32), vol_ma50)

    vol_ma20 = _rolling_mean(volume.astype(np.float32), 20)
    vol_std20 = _rolling_std(volume.astype(np.float32), 20)
    f_vol_std = _safe_div(vol_std20, vol_ma20)  # coefficient of variation

    vol_ma50_ratio_lag5 = _lag(f_vol_ma50_ratio, 5)
    f_vol_ma50_slope = (f_vol_ma50_ratio - vol_ma50_ratio_lag5).astype(np.float32)

    # ==========================================================================
    # Group H — Lag features for top 8 existing features (24 new features)
    # ==========================================================================
    # f_atr_ratio lags
    f_atr_ratio_l1 = _lag(f_atr_ratio,    1)
    f_atr_ratio_l3 = _lag(f_atr_ratio,    3)
    f_atr_ratio_l5 = _lag(f_atr_ratio,    5)
    # f_adx lags
    f_adx_l1 = _lag(f_adx,  1)
    f_adx_l3 = _lag(f_adx,  3)
    f_adx_l5 = _lag(f_adx,  5)
    # f_pdi lags
    f_pdi_l1 = _lag(f_pdi,  1)
    f_pdi_l3 = _lag(f_pdi,  3)
    f_pdi_l5 = _lag(f_pdi,  5)
    # f_ndi lags
    f_ndi_l1 = _lag(f_ndi,  1)
    f_ndi_l3 = _lag(f_ndi,  3)
    f_ndi_l5 = _lag(f_ndi,  5)
    # f_rsi lags
    f_rsi_l1 = _lag(f_rsi,  1)
    f_rsi_l3 = _lag(f_rsi,  3)
    f_rsi_l5 = _lag(f_rsi,  5)
    # f_vol_ratio lags
    f_vol_ratio_l1 = _lag(f_vol_ratio, 1)
    f_vol_ratio_l3 = _lag(f_vol_ratio, 3)
    f_vol_ratio_l5 = _lag(f_vol_ratio, 5)
    # f_ema5_vs_ema20 lags
    f_ema5_vs_ema20_l1 = _lag(f_ema5_vs_ema20, 1)
    f_ema5_vs_ema20_l3 = _lag(f_ema5_vs_ema20, 3)
    f_ema5_vs_ema20_l5 = _lag(f_ema5_vs_ema20, 5)
    # f_bb_pos lags
    f_bb_pos_l1 = _lag(f_bb_pos, 1)
    f_bb_pos_l3 = _lag(f_bb_pos, 3)
    f_bb_pos_l5 = _lag(f_bb_pos, 5)

    # ==========================================================================
    # Group I — Extended EMA structure (10 new features)
    # ==========================================================================
    ema10 = compute_ema(close, 10)
    ema10_32 = ema10.astype(np.float32)
    ema100 = compute_ema(close, 100)
    ema100_32 = ema100.astype(np.float32)

    f_ema10_vs_ema20  = _safe_div((ema10_32 - ema20).astype(np.float32), atr14)
    f_ema5_vs_ema60   = _safe_div((ema5  - ema60).astype(np.float32), atr14)
    f_ema20_vs_ema100 = _safe_div((ema20 - ema100_32).astype(np.float32), close32) * 100.0
    f_ema100_vs_ema200 = _safe_div((ema100_32 - ema200).astype(np.float32), close32) * 100.0
    f_close_vs_ema60  = _safe_div((close32 - ema60).astype(np.float32), atr14)
    f_close_vs_ema100 = _safe_div((close32 - ema100_32).astype(np.float32), close32) * 100.0

    ema10_lag5 = _lag(ema10_32, 5)
    f_ema10_slope = _safe_div((ema10_32 - ema10_lag5).astype(np.float32), atr14) / 5.0

    ema60_lag5 = _lag(ema60, 5)
    f_ema60_slope = _safe_div((ema60 - ema60_lag5).astype(np.float32), atr14) / 5.0

    # EMA alignment score: count of (ema5>ema10, ema10>ema20, ema20>ema60, ema60>ema200) that hold
    f_ema_align_bull = (
        (ema5 > ema10_32).astype(np.float32) +
        (ema10_32 > ema20).astype(np.float32) +
        (ema20 > ema60).astype(np.float32) +
        (ema60 > ema200).astype(np.float32)
    )
    f_ema_align_bear = (
        (ema5 < ema10_32).astype(np.float32) +
        (ema10_32 < ema20).astype(np.float32) +
        (ema20 < ema60).astype(np.float32) +
        (ema60 < ema200).astype(np.float32)
    )

    # ==========================================================================
    # Group J — RSI divergence & momentum cross (8 new features)
    # ==========================================================================
    # RSI overbought/oversold zones
    f_rsi_ob = (rsi14 > 70.0).astype(np.float32)
    f_rsi_os = (rsi14 < 30.0).astype(np.float32)
    f_rsi7_ob = (f_rsi7 > 70.0).astype(np.float32)
    f_rsi7_os = (f_rsi7 < 30.0).astype(np.float32)

    # Price momentum: (close - close_N) / (ATR * N) — additional periods
    close_lag2  = _lag(close32, 2)
    close_lag7  = _lag(close32, 7)
    f_ret2  = _safe_div((close32 - close_lag2).astype(np.float32), atr14)
    f_ret7  = _safe_div((close32 - close_lag7).astype(np.float32), atr14)

    # Acceleration: change of 5-bar return
    f_ret5_lag5  = _lag(f_ret5,  5)
    f_ret_accel  = (f_ret5 - f_ret5_lag5).astype(np.float32)

    # Distance from Stoch crossover zone
    f_stoch_spread = (f_stoch_k - f_stoch_d).astype(np.float32)

    # ==========================================================================
    # Group K — Volatility regime & Bollinger enrichment (10 new features)
    # ==========================================================================
    # BB squeeze: close within narrow band
    f_bb_squeeze = (f_bb_width < pd.Series(f_bb_width).rolling(20, min_periods=1).mean().values.astype(np.float32)).astype(np.float32)

    # BB width slope
    bb_width_lag5 = _lag(f_bb_width, 5)
    f_bb_width_slope = ((f_bb_width - bb_width_lag5) / 5.0).astype(np.float32)

    # ATR percentile rank over last 100 bars (normalized 0-1)
    atr_rolling100 = pd.Series(atr14).rolling(100, min_periods=2)
    atr_pct_rank = atr_rolling100.rank(pct=True).fillna(0.5).values.astype(np.float32)
    f_atr_pct_rank = atr_pct_rank

    # ATR expansion: current ATR above its 50-bar mean
    atr_ma50 = _rolling_mean(atr14, 50)
    f_atr_expansion = _safe_div(atr14, atr_ma50)

    # Distance to BB upper and lower in ATR units
    f_dist_bb_upper = _safe_div((bb_upper - close32).astype(np.float32), atr14)
    f_dist_bb_lower = _safe_div((close32 - bb_lower).astype(np.float32), atr14)

    # BB midline slope
    bb_mid_lag5 = _lag(bb_mid, 5)
    f_bb_mid_slope = _safe_div((bb_mid - bb_mid_lag5).astype(np.float32), atr14) / 5.0

    # Keltner channel position (using ATR multiplier 1.5)
    kc_upper = ema20 + 1.5 * atr14
    kc_lower = ema20 - 1.5 * atr14
    kc_range = kc_upper - kc_lower
    kc_range_safe = np.where(kc_range > 0, kc_range, np.ones_like(kc_range)).astype(np.float32)
    f_kc_pos = _safe_div((close32 - kc_lower).astype(np.float32), kc_range_safe)

    # True range ratio (current TR / ATR14)
    tr_arr = np.empty(n, dtype=np.float32)
    tr_arr[0] = float(high32[0] - low32[0])
    for _i in range(1, n):
        _a = high32[_i] - low32[_i]
        _b = abs(high32[_i] - close32[_i - 1])
        _c = abs(low32[_i]  - close32[_i - 1])
        tr_arr[_i] = _a if _a >= _b and _a >= _c else (_b if _b >= _c else _c)
    f_tr_ratio = _safe_div(tr_arr, atr14)

    # ==========================================================================
    # Group L — Cross-indicator interaction features (10 new features)
    # ==========================================================================
    # Trend strength × momentum
    f_adx_x_di_spread  = (f_adx * f_di_spread / 100.0).astype(np.float32)  # normalized
    f_adx_x_rsi_vs50   = (f_adx * f_rsi_vs_50 / 100.0).astype(np.float32)

    # Volume confirmation of trend
    f_vol_x_ret5       = (f_vol_ratio * f_ret5).astype(np.float32)
    f_vol_x_rsi_vs50   = (f_vol_ratio * f_rsi_vs_50 / 50.0).astype(np.float32)

    # RSI momentum × ATR expansion
    f_rsi_vs50_x_atr   = (f_rsi_vs_50 / 50.0 * f_atr_ratio).astype(np.float32)

    # MACD × volume
    macd_norm = _safe_div(f_macd_hist, atr14)
    f_macd_x_vol       = (macd_norm * f_vol_ratio).astype(np.float32)

    # Stochastic confirmation
    f_stoch_k_vs50     = (f_stoch_k - 50.0).astype(np.float32)
    f_stoch_d_vs50     = (f_stoch_d - 50.0).astype(np.float32)

    # EMA alignment × ADX
    f_ema_align_x_adx  = (f_ema_align_bull * f_adx / 100.0).astype(np.float32)

    # Price structure: inside bar (high <= prev_high AND low >= prev_low)
    prev_high = _lag(high32, 1)
    prev_low  = _lag(low32,  1)
    f_inside_bar = ((high32 <= prev_high) & (low32 >= prev_low)).astype(np.float32)

    # ==========================================================================
    # Group M — Additional RSI/momentum lags (20 new features)
    # ==========================================================================
    # RSI50 lags
    f_rsi50_l1 = _lag(f_rsi50, 1)
    f_rsi50_l3 = _lag(f_rsi50, 3)
    f_rsi50_l5 = _lag(f_rsi50, 5)

    # MACD histogram lags
    f_macd_hist_l1 = _lag(f_macd_hist, 1)
    f_macd_hist_l3 = _lag(f_macd_hist, 3)
    f_macd_hist_l5 = _lag(f_macd_hist, 5)

    # Stoch K lags
    f_stoch_k_l1 = _lag(f_stoch_k, 1)
    f_stoch_k_l3 = _lag(f_stoch_k, 3)
    f_stoch_k_l5 = _lag(f_stoch_k, 5)

    # ADX21 lags
    f_adx21_l1 = _lag(f_adx21, 1)
    f_adx21_l3 = _lag(f_adx21, 3)
    f_adx21_l5 = _lag(f_adx21, 5)

    # ret10 lags
    f_ret10_l1 = _lag(f_ret10, 1)
    f_ret10_l3 = _lag(f_ret10, 3)
    f_ret10_l5 = _lag(f_ret10, 5)

    # CCI14 lags
    f_cci14_l1 = _lag(f_cci14, 1)
    f_cci14_l3 = _lag(f_cci14, 3)
    f_cci14_l5 = _lag(f_cci14, 5)

    # Williams %R lags
    f_williams_r_l1 = _lag(f_williams_r, 1)
    f_williams_r_l3 = _lag(f_williams_r, 3)

    # ==========================================================================
    # Group N — Additional price/session features (25 new features)
    # ==========================================================================
    # RSI × volume interaction
    f_rsi_x_vol = (f_rsi_vs_50 / 50.0 * f_vol_ratio).astype(np.float32)

    # Close vs Open as % of close
    f_close_vs_open_pct = _safe_div((close32 - open32).astype(np.float32), close32) * 100.0

    # Gap open: (open[i] - close[i-1]) / ATR
    f_gap_open = _safe_div((open32 - close_lag1).astype(np.float32), atr14)

    # High-low range as % of close
    f_hl_range_pct = _safe_div((high32 - low32).astype(np.float32), close32) * 100.0

    # Volume × price direction (signed volume pressure)
    price_dir = np.sign(close32 - open32).astype(np.float32)
    f_vol_price_trend = (f_vol_ratio * price_dir).astype(np.float32)

    # Return std slope: 5-bar std minus 3-bar-lagged 5-bar std
    ret_std5_lag3 = _lag(f_ret_std5, 3)
    f_ret_std5_slope = (f_ret_std5 - ret_std5_lag3).astype(np.float32)

    # 15m RSI already relative to 50
    f_rsi14_15m_vs50 = (f_rsi14_15m - 50.0).astype(np.float32)

    # ADX14 vs ADX21 divergence
    f_adx_diff = (f_adx - f_adx21).astype(np.float32)

    # Directional strength (bullish = pdi - ndi clamped positive / ADX)
    adx_safe = np.where(f_adx > 0, f_adx, np.ones_like(f_adx)).astype(np.float32)
    f_di_bull_strength = _safe_div(np.maximum(f_di_spread, 0.0), adx_safe)
    f_di_bear_strength = _safe_div(np.maximum(-f_di_spread, 0.0), adx_safe)

    # RSI7 × RSI21 product (normalized)
    f_rsi7_x_rsi21 = ((f_rsi7 - 50.0) * (f_rsi21 - 50.0) / 2500.0).astype(np.float32)

    # CCI14 vs CCI20 delta
    f_cci14_vs_cci20 = (f_cci14 - f_cci20).astype(np.float32)

    # BB position slope
    bb_pos_lag5 = _lag(f_bb_pos, 5)
    f_bb_pos_slope = ((f_bb_pos - bb_pos_lag5) / 5.0).astype(np.float32)

    # Rolling std of close price (not returns) normalized by ATR
    close_roll_std10 = _rolling_std(close32, 10)
    close_roll_std20 = _rolling_std(close32, 20)
    f_close_roll_std10 = _safe_div(close_roll_std10, atr14)
    f_close_roll_std20 = _safe_div(close_roll_std20, atr14)

    # Longer lag features
    f_atr_ratio_l10 = _lag(f_atr_ratio, 10)
    f_rsi_l10       = _lag(f_rsi,       10)
    f_vol_ratio_l10 = _lag(f_vol_ratio, 10)
    f_ret5_l5       = _lag(f_ret5,       5)
    f_ret5_l10      = _lag(f_ret5,      10)

    # Period-pair comparisons
    f_adx_vs_adx21  = (f_adx  - f_adx21).astype(np.float32)   # same as f_adx_diff but kept named
    f_pdi_vs_pdi21  = (f_pdi  - f_pdi21).astype(np.float32)
    f_ndi_vs_ndi21  = (f_ndi  - f_ndi21).astype(np.float32)
    f_atr7_vs_atr21 = (f_atr7_ratio - f_atr21_ratio).astype(np.float32)

    # Squeeze duration as fraction of 20-bar window (already 0-20, normalize to 0-1)
    f_squeeze_dur_pct = (f_squeeze_dur / 20.0).astype(np.float32)

    # ==========================================================================
    # Assemble result DataFrame
    # ==========================================================================
    feat_arrays = {
        # --- Group 1 (15) ---
        "f_atr_ratio":       f_atr_ratio,
        "f_atr_ratio_lag1":  f_atr_ratio_lag1,
        "f_atr_ratio_lag2":  f_atr_ratio_lag2,
        "f_atr_ratio_slope": f_atr_ratio_slope,
        "f_atr_pct":         f_atr_pct,
        "f_bb_width":        f_bb_width,
        "f_atr_vs_7d":       f_atr_vs_7d,
        "f_squeeze_dur":     f_squeeze_dur,
        "f_vol_ratio":       f_vol_ratio,
        "f_vol_ratio_lag1":  f_vol_ratio_lag1,
        "f_vol_spike":       f_vol_spike,
        "f_body_ratio":      f_body_ratio,
        "f_upper_wick_atr":  f_upper_wick_atr,
        "f_lower_wick_atr":  f_lower_wick_atr,
        "f_engulfing":       f_engulfing,
        # --- Group 2 (15) ---
        "f_adx":             f_adx,
        "f_adx_lag5":        f_adx_lag5,
        "f_adx_slope":       f_adx_slope,
        "f_pdi":             f_pdi,
        "f_ndi":             f_ndi,
        "f_di_spread":       f_di_spread,
        "f_di_spread_abs":   f_di_spread_abs,
        "f_ema5_vs_ema20":   f_ema5_vs_ema20,
        "f_ema20_vs_ema60":  f_ema20_vs_ema60,
        "f_ema20_vs_ema200": f_ema20_vs_ema200,
        "f_close_vs_ema200": f_close_vs_ema200,
        "f_close_vs_ema20":  f_close_vs_ema20,
        "f_ema5_slope":      f_ema5_slope,
        "f_ema20_slope":     f_ema20_slope,
        "f_recent_high_dist":f_recent_high_dist,
        "f_recent_low_dist": f_recent_low_dist,
        # --- Group 3 (10) ---
        "f_rsi":             f_rsi,
        "f_rsi_ma5":         f_rsi_ma5,
        "f_rsi_slope":       f_rsi_slope,
        "f_rsi_vs_50":       f_rsi_vs_50,
        "f_ret1":            f_ret1,
        "f_ret3":            f_ret3,
        "f_ret5":            f_ret5,
        "f_ret10":           f_ret10,
        "f_ret20":           f_ret20,
        "f_ret_vs_atr":      f_ret_vs_atr,
        # --- Group 4 (5) ---
        "f_hour_sin":            f_hour_sin,
        "f_hour_cos":            f_hour_cos,
        "f_minute_in_session":   f_minute_in_session,
        "f_day_of_week":         f_day_of_week,
        "f_is_afternoon":        f_is_afternoon,
        # --- Group 5 (10) ---
        "f_rsi7":                f_rsi7,
        "f_rsi21":               f_rsi21,
        "f_adx7":                f_adx7,
        "f_atr7_ratio":          f_atr7_ratio,
        "f_atr7_pct":            f_atr7_pct,
        "f_bb_pos":              f_bb_pos,
        "f_close_vs_bb_mid":     f_close_vs_bb_mid,
        "f_vol_slope":           f_vol_slope,
        "f_consecutive_up":      f_consecutive_up,
        "f_bar_count_in_session":f_bar_count_in_session,
        # --- Group A: Multi-period RSI (6 new; f_rsi7/f_rsi21 already in Group 5) ---
        "f_rsi50":               f_rsi50,
        "f_rsi7_slope":          f_rsi7_slope,
        "f_rsi21_slope":         f_rsi21_slope,
        "f_rsi7_vs_rsi21":       f_rsi7_vs_rsi21,
        "f_rsi14_vs_rsi50":      f_rsi14_vs_rsi50,
        # --- Group B: Multi-period ATR/ADX (7 new) ---
        "f_adx21":               f_adx21,
        "f_pdi21":               f_pdi21,
        "f_ndi21":               f_ndi21,
        "f_di_spread21":         f_di_spread21,
        "f_adx21_slope":         f_adx21_slope,
        "f_atr21_ratio":         f_atr21_ratio,
        "f_atr7_slope":          f_atr7_slope,
        "f_atr_diff":            f_atr_diff,
        # --- Group C: MACD, Stochastic, Williams, CCI (8 new) ---
        "f_macd_hist":           f_macd_hist,
        "f_macd_signal":         f_macd_signal,
        "f_macd_slope":          f_macd_slope,
        "f_stoch_k":             f_stoch_k,
        "f_stoch_d":             f_stoch_d,
        "f_williams_r":          f_williams_r,
        "f_cci14":               f_cci14,
        "f_cci20":               f_cci20,
        # --- Group D: Multi-timeframe 15-min (5 new) ---
        "f_rsi14_15m":           f_rsi14_15m,
        "f_adx14_15m":           f_adx14_15m,
        "f_atr_ratio_15m":       f_atr_ratio_15m,
        "f_ema20_slope_15m":     f_ema20_slope_15m,
        "f_rsi_vs50_15m":        f_rsi_vs50_15m,
        # --- Group E: Rolling return statistics (5 new) ---
        "f_ret_std5":            f_ret_std5,
        "f_ret_std10":           f_ret_std10,
        "f_ret_std20":           f_ret_std20,
        "f_ret_skew10":          f_ret_skew10,
        "f_ret_skew20":          f_ret_skew20,
        # --- Group F: Additional price structure (6 new) ---
        "f_dist_recent_high200": f_dist_recent_high200,
        "f_dist_recent_low200":  f_dist_recent_low200,
        "f_range_ratio":         f_range_ratio,
        "f_open_vs_close":       f_open_vs_close,
        "f_high_vs_ema20":       f_high_vs_ema20,
        "f_low_vs_ema20":        f_low_vs_ema20,
        # --- Group G: Volume enriched (3 new) ---
        "f_vol_ma50_ratio":      f_vol_ma50_ratio,
        "f_vol_std":             f_vol_std,
        "f_vol_ma50_slope":      f_vol_ma50_slope,
        # --- Group H: Lag features (24 new) ---
        "f_atr_ratio_l1":        f_atr_ratio_l1,
        "f_atr_ratio_l3":        f_atr_ratio_l3,
        "f_atr_ratio_l5":        f_atr_ratio_l5,
        "f_adx_l1":              f_adx_l1,
        "f_adx_l3":              f_adx_l3,
        "f_adx_l5":              f_adx_l5,
        "f_pdi_l1":              f_pdi_l1,
        "f_pdi_l3":              f_pdi_l3,
        "f_pdi_l5":              f_pdi_l5,
        "f_ndi_l1":              f_ndi_l1,
        "f_ndi_l3":              f_ndi_l3,
        "f_ndi_l5":              f_ndi_l5,
        "f_rsi_l1":              f_rsi_l1,
        "f_rsi_l3":              f_rsi_l3,
        "f_rsi_l5":              f_rsi_l5,
        "f_vol_ratio_l1":        f_vol_ratio_l1,
        "f_vol_ratio_l3":        f_vol_ratio_l3,
        "f_vol_ratio_l5":        f_vol_ratio_l5,
        "f_ema5_vs_ema20_l1":    f_ema5_vs_ema20_l1,
        "f_ema5_vs_ema20_l3":    f_ema5_vs_ema20_l3,
        "f_ema5_vs_ema20_l5":    f_ema5_vs_ema20_l5,
        "f_bb_pos_l1":           f_bb_pos_l1,
        "f_bb_pos_l3":           f_bb_pos_l3,
        "f_bb_pos_l5":           f_bb_pos_l5,
        # --- Group I: Extended EMA structure (10 new) ---
        "f_ema10_vs_ema20":      f_ema10_vs_ema20,
        "f_ema5_vs_ema60":       f_ema5_vs_ema60,
        "f_ema20_vs_ema100":     f_ema20_vs_ema100,
        "f_ema100_vs_ema200":    f_ema100_vs_ema200,
        "f_close_vs_ema60":      f_close_vs_ema60,
        "f_close_vs_ema100":     f_close_vs_ema100,
        "f_ema10_slope":         f_ema10_slope,
        "f_ema60_slope":         f_ema60_slope,
        "f_ema_align_bull":      f_ema_align_bull,
        "f_ema_align_bear":      f_ema_align_bear,
        # --- Group J: RSI divergence & momentum cross (8 new) ---
        "f_rsi_ob":              f_rsi_ob,
        "f_rsi_os":              f_rsi_os,
        "f_rsi7_ob":             f_rsi7_ob,
        "f_rsi7_os":             f_rsi7_os,
        "f_ret2":                f_ret2,
        "f_ret7":                f_ret7,
        "f_ret_accel":           f_ret_accel,
        "f_stoch_spread":        f_stoch_spread,
        # --- Group K: Volatility regime & Bollinger enrichment (10 new) ---
        "f_bb_squeeze":          f_bb_squeeze,
        "f_bb_width_slope":      f_bb_width_slope,
        "f_atr_pct_rank":        f_atr_pct_rank,
        "f_atr_expansion":       f_atr_expansion,
        "f_dist_bb_upper":       f_dist_bb_upper,
        "f_dist_bb_lower":       f_dist_bb_lower,
        "f_bb_mid_slope":        f_bb_mid_slope,
        "f_kc_pos":              f_kc_pos,
        "f_tr_ratio":            f_tr_ratio,
        # --- Group L: Cross-indicator interaction features (10 new) ---
        "f_adx_x_di_spread":     f_adx_x_di_spread,
        "f_adx_x_rsi_vs50":      f_adx_x_rsi_vs50,
        "f_vol_x_ret5":          f_vol_x_ret5,
        "f_vol_x_rsi_vs50":      f_vol_x_rsi_vs50,
        "f_rsi_vs50_x_atr":      f_rsi_vs50_x_atr,
        "f_macd_x_vol":          f_macd_x_vol,
        "f_stoch_k_vs50":        f_stoch_k_vs50,
        "f_stoch_d_vs50":        f_stoch_d_vs50,
        "f_ema_align_x_adx":     f_ema_align_x_adx,
        "f_inside_bar":          f_inside_bar,
        # --- Group M: Additional RSI/momentum lags (20 new) ---
        "f_rsi50_l1":            f_rsi50_l1,
        "f_rsi50_l3":            f_rsi50_l3,
        "f_rsi50_l5":            f_rsi50_l5,
        "f_macd_hist_l1":        f_macd_hist_l1,
        "f_macd_hist_l3":        f_macd_hist_l3,
        "f_macd_hist_l5":        f_macd_hist_l5,
        "f_stoch_k_l1":          f_stoch_k_l1,
        "f_stoch_k_l3":          f_stoch_k_l3,
        "f_stoch_k_l5":          f_stoch_k_l5,
        "f_adx21_l1":            f_adx21_l1,
        "f_adx21_l3":            f_adx21_l3,
        "f_adx21_l5":            f_adx21_l5,
        "f_ret10_l1":            f_ret10_l1,
        "f_ret10_l3":            f_ret10_l3,
        "f_ret10_l5":            f_ret10_l5,
        "f_cci14_l1":            f_cci14_l1,
        "f_cci14_l3":            f_cci14_l3,
        "f_cci14_l5":            f_cci14_l5,
        "f_williams_r_l1":       f_williams_r_l1,
        "f_williams_r_l3":       f_williams_r_l3,
        # --- Group N: Additional price/session features (25 new) ---
        "f_rsi_x_vol":           f_rsi_x_vol,
        "f_close_vs_open_pct":   f_close_vs_open_pct,
        "f_gap_open":            f_gap_open,
        "f_hl_range_pct":        f_hl_range_pct,
        "f_vol_price_trend":     f_vol_price_trend,
        "f_ret_std5_slope":      f_ret_std5_slope,
        "f_rsi14_15m_vs50":      f_rsi14_15m_vs50,
        "f_adx_diff":            f_adx_diff,
        "f_di_bull_strength":    f_di_bull_strength,
        "f_di_bear_strength":    f_di_bear_strength,
        "f_rsi7_x_rsi21":        f_rsi7_x_rsi21,
        "f_cci14_vs_cci20":      f_cci14_vs_cci20,
        "f_bb_pos_slope":        f_bb_pos_slope,
        "f_close_roll_std10":    f_close_roll_std10,
        "f_close_roll_std20":    f_close_roll_std20,
        "f_atr_ratio_l10":       f_atr_ratio_l10,
        "f_rsi_l10":             f_rsi_l10,
        "f_vol_ratio_l10":       f_vol_ratio_l10,
        "f_ret5_l5":             f_ret5_l5,
        "f_ret5_l10":            f_ret5_l10,
        "f_adx_vs_adx21":        f_adx_vs_adx21,
        "f_pdi_vs_pdi21":        f_pdi_vs_pdi21,
        "f_ndi_vs_ndi21":        f_ndi_vs_ndi21,
        "f_atr7_vs_atr21":       f_atr7_vs_atr21,
        "f_squeeze_dur_pct":     f_squeeze_dur_pct,
    }

    # Validate all arrays are length n
    for fname, arr in feat_arrays.items():
        if len(arr) != n:
            raise ValueError(f"Feature {fname} has length {len(arr)}, expected {n}")

    # Build DataFrame
    result = pd.DataFrame({"datetime": datetimes.values, "instrument": instrument})
    for fname, arr in feat_arrays.items():
        result[fname] = arr.astype(np.float32)

    # Handle NaN: fill forward then fill remaining with 0 (first ~50 bars of warmup)
    feat_cols = [c for c in result.columns if c.startswith("f_")]
    result[feat_cols] = result[feat_cols].ffill().fillna(0.0)

    # Update module-level FEATURE_NAMES on first call
    if not FEATURE_NAMES:
        FEATURE_NAMES.extend(feat_cols)

    return result


# ---------------------------------------------------------------------------
# Batch processor
# ---------------------------------------------------------------------------

def build_all_features(data_dir: Path) -> dict:
    """Load each {instrument}_5m.parquet, compute features, save {instrument}_features.parquet.

    Returns:
        Dict mapping instrument name -> output parquet path (str).
    """
    data_dir = Path(data_dir)
    results  = {}
    errors   = {}

    parquet_files = sorted(data_dir.glob("*_5m.parquet"))
    if not parquet_files:
        print(f"No *_5m.parquet files found in {data_dir}")
        return results

    print(f"\n=== Stage 2: Feature Engineering ===")
    print(f"Data directory: {data_dir}")
    print(f"Found {len(parquet_files)} instrument(s): {[p.stem.replace('_5m','') for p in parquet_files]}\n")

    for parquet_path in parquet_files:
        instrument = parquet_path.stem.replace("_5m", "")
        out_path   = data_dir / f"{instrument}_features.parquet"

        print(f"[{instrument}] Loading {parquet_path.name} ...")
        t0 = time.perf_counter()

        try:
            df_5m = pd.read_parquet(parquet_path)
            df_5m["datetime"] = pd.to_datetime(df_5m["datetime"])
            n = len(df_5m)
            print(f"  {n:,} bars  ({df_5m['datetime'].iloc[0]} ~ {df_5m['datetime'].iloc[-1]})")

            # Precompute all indicators via gpu_indicators
            print(f"  Precomputing indicators ...")
            ind = precompute_all(df_5m, verbose=True)

            # Build feature matrix
            print(f"  Building feature matrix ...")
            feat_df = build_feature_matrix(df_5m, ind, instrument)

            # Save
            feat_df.to_parquet(out_path, index=False)
            elapsed  = time.perf_counter() - t0
            size_mb  = out_path.stat().st_size / 1024 / 1024
            n_feats  = len([c for c in feat_df.columns if c.startswith("f_")])
            print(f"[{instrument}] Saved {len(feat_df):,} rows x {n_feats} features "
                  f"-> {out_path.name} ({size_mb:.1f} MB, {elapsed:.1f}s)")
            results[instrument] = str(out_path)

        except Exception as e:
            elapsed = time.perf_counter() - t0
            import traceback
            print(f"[{instrument}] ERROR after {elapsed:.1f}s: {e}")
            traceback.print_exc()
            errors[instrument] = str(e)

    print("\n=== Feature engineering complete ===")
    if errors:
        print(f"Errors ({len(errors)}):")
        for name, msg in errors.items():
            print(f"  {name}: {msg}")
    else:
        print(f"All {len(results)} instrument(s) processed successfully.")

    return results


# ---------------------------------------------------------------------------
# Feature selection utility (XGBoost importance-based)
# ---------------------------------------------------------------------------

def select_features(df_signals: pd.DataFrame, n_top: int = 70) -> list:
    """Use XGBoost importance to select top n_top features from all f_* and fd_* columns.

    Args:
        df_signals: DataFrame that must contain a 'win' column (0/1 label)
                    and feature columns starting with 'f_' or 'fd_'.
        n_top:      Number of top features to return.

    Returns:
        List of feature column names sorted by descending importance.
    """
    from xgboost import XGBClassifier

    feature_cols = [c for c in df_signals.columns if c.startswith(("f_", "fd_"))]
    X = df_signals[feature_cols].fillna(0).values.astype("float32")
    y = df_signals["win"].astype(int).values

    model = XGBClassifier(
        n_estimators=100,
        max_depth=4,
        random_state=42,
        device="cpu",
        verbosity=0,
    )
    model.fit(X, y)

    importances = pd.Series(model.feature_importances_, index=feature_cols)
    selected = importances.nlargest(n_top).index.tolist()
    print(f"Feature selection: {len(feature_cols)} -> {len(selected)} features")
    return selected


SELECTED_FEATURES = None  # set after running select_features()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Stage 2: Compute ML features from 5-min OHLCV parquet files"
    )
    parser.add_argument(
        "--data-dir",
        default=str(ROOT / "data" / "historical" / "ml"),
        help="Directory containing {instrument}_5m.parquet files (default: data/historical/ml/)",
    )
    parser.add_argument(
        "--instrument",
        default=None,
        help="Process only this instrument (e.g. BTCUSDT). Default: all.",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)

    if args.instrument:
        inst = args.instrument.upper()
        parquet_path = data_dir / f"{inst}_5m.parquet"
        if not parquet_path.exists():
            print(f"File not found: {parquet_path}")
            sys.exit(1)
        # Process single instrument
        print(f"\n=== Stage 2: Feature Engineering ({inst}) ===")
        t0 = time.perf_counter()
        df_5m = pd.read_parquet(parquet_path)
        df_5m["datetime"] = pd.to_datetime(df_5m["datetime"])
        ind = precompute_all(df_5m, verbose=True)
        feat_df = build_feature_matrix(df_5m, ind, inst)
        out_path = data_dir / f"{inst}_features.parquet"
        feat_df.to_parquet(out_path, index=False)
        n_feats = len([c for c in feat_df.columns if c.startswith("f_")])
        print(f"Saved -> {out_path}  ({n_feats} features, {time.perf_counter() - t0:.1f}s)")
    else:
        build_all_features(data_dir)


if __name__ == "__main__":
    main()
