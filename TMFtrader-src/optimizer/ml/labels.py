"""
Stage 3: TMF Breakout — Signal Detection + Labeling
=====================================================
Runs a self-contained Mode-A breakout detector on each instrument's 5-min
historical data, simulates trade exits, joins features at entry bar, and
saves a labeled ML dataset.

Pipeline position:
  Stage 1: fetch_historical → {instrument}_5m.parquet
  Stage 2: features.py      → {instrument}_features.parquet
  Stage 3: labels.py  (this)→ {instrument}_signals.parquet + ml_dataset.parquet
  Stage 4: cv.py            → cross-validation helpers
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from datetime import time
from typing import Optional

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Root paths
# ─────────────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent.parent
ML_DATA_DIR = ROOT / "data" / "historical" / "ml"

# ─────────────────────────────────────────────────────────────────────────────
# Instrument configurations
# ─────────────────────────────────────────────────────────────────────────────
_TMF_CFG = {
    "session_start": time(8, 45),
    "session_end": time(13, 30),
    "afternoon_time": time(11, 0),
    "min_adx": 23.0,
    "afternoon_min_adx": 30.0,
    "squeeze_ratio": 0.90,
    "expand_ratio": 1.18,
    "squeeze_grace_bars": 1,
    "sl_atr": 2.5,
    "tp_atr": 10.0,
    "trail_trigger_atr": 1.2,
    "trail_dist_atr": 1.25,
    "max_bars": 80,
    "early_cut_bars": 40,
    "early_cut_loss_atr": 1.5,
    "min_di_gap": 10.0,
    "momentum_rsi_bull": 52.0,
    "momentum_rsi_bear": 46.0,
    "trend_filter": True,
}

_CRYPTO_CFG = {
    "session_start": time(8, 0),
    "session_end": time(16, 0),
    "afternoon_time": time(13, 0),
    "min_adx": 20.0,
    "afternoon_min_adx": 25.0,
    "squeeze_ratio": 0.90,
    "expand_ratio": 1.18,
    "squeeze_grace_bars": 1,
    "sl_atr": 2.5,
    "tp_atr": 10.0,
    "trail_trigger_atr": 1.2,
    "trail_dist_atr": 1.25,
    "max_bars": 80,
    "early_cut_bars": 40,
    "early_cut_loss_atr": 1.5,
    "min_di_gap": 10.0,
    "momentum_rsi_bull": 50.0,
    "momentum_rsi_bear": 50.0,
    "trend_filter": True,
}

# US equity index ETFs: session in naive UTC
_US_ETF_CFG = {
    "session_start": time(14, 30),   # 09:30 ET = 14:30 UTC (covers both DST seasons)
    "session_end":   time(20, 55),   # 16:00 ET = 21:00 UTC
    "afternoon_time": time(18, 0),   # ~13:00 ET — afternoon tightening
    "min_adx": 20.0,
    "afternoon_min_adx": 25.0,
    "squeeze_ratio": 0.90,
    "expand_ratio": 1.18,
    "squeeze_grace_bars": 1,
    "sl_atr": 2.5,
    "tp_atr": 10.0,
    "trail_trigger_atr": 1.2,
    "trail_dist_atr": 1.25,
    "max_bars": 80,
    "early_cut_bars": 40,
    "early_cut_loss_atr": 1.5,
    "min_di_gap": 10.0,
    "momentum_rsi_bull": 52.0,
    "momentum_rsi_bear": 48.0,
    "trend_filter": True,
}

# US index CFDs (USTEC = Nasdaq 100 CFD, US500 = S&P 500 CFD) and leveraged ETFs (SPXL)
# Same session as US ETFs; SPXL is 3x leveraged like TMF so it gets identical ADX thresholds
_US_INDEX_CFG = {
    "session_start": time(14, 30),
    "session_end":   time(20, 55),
    "afternoon_time": time(18, 0),
    "min_adx": 20.0,
    "afternoon_min_adx": 25.0,
    "squeeze_ratio": 0.90,
    "expand_ratio": 1.18,
    "squeeze_grace_bars": 1,
    "sl_atr": 2.5,
    "tp_atr": 10.0,
    "trail_trigger_atr": 1.2,
    "trail_dist_atr": 1.25,
    "max_bars": 80,
    "early_cut_bars": 40,
    "early_cut_loss_atr": 1.5,
    "min_di_gap": 10.0,
    "momentum_rsi_bull": 52.0,
    "momentum_rsi_bear": 48.0,
    "trend_filter": True,
}

INSTRUMENT_CONFIGS: dict[str, dict] = {
    "TMF":      _TMF_CFG,
    "TX":       _TMF_CFG.copy(),
    # ── Leveraged ETFs (same product structure as TMF) ────────────────────────
    "SPXL":     _US_INDEX_CFG,           # S&P 500 3x leveraged
    "TECL":     _US_INDEX_CFG.copy(),    # Technology sector 3x
    "TQQQ":     _US_INDEX_CFG.copy(),    # Nasdaq 100 3x — closest to TMF
    # ── Non-leveraged index ETFs ──────────────────────────────────────────────
    "QQQM":     _US_ETF_CFG,             # Nasdaq 100 ETF
    "IVV":      _US_ETF_CFG.copy(),      # S&P 500 ETF
    # ── Sector ETFs (Nasdaq-correlated) ───────────────────────────────────────
    "XLK":      _US_ETF_CFG.copy(),      # S&P 500 IT sector
    "SOXX":     _US_ETF_CFG.copy(),      # Semiconductor (SOX proxy)
    "IBB":      _US_ETF_CFG.copy(),      # Nasdaq Biotech (NBI proxy)
    "FNGS":     _US_ETF_CFG.copy(),      # NYSE FANG+ ETF
    # ── Nasdaq 100 Index CFD ──────────────────────────────────────────────────
    "NAS100":   _US_INDEX_CFG.copy(),    # NDX CFD — broker name varies (NAS100/USTEC/NDX100)
    # ── Crypto: BTC only (altcoins dropped — low Nasdaq correlation) ──────────
    "BTCUSDT":  _CRYPTO_CFG,
}

# ─────────────────────────────────────────────────────────────────────────────
# Indicator helpers (self-contained — no external strategy imports)
# ─────────────────────────────────────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing (used by ATR / ADX)."""
    result = series.copy().astype(float)
    alpha = 1.0 / period
    for i in range(1, len(result)):
        result.iloc[i] = result.iloc[i - 1] * (1 - alpha) + result.iloc[i] * alpha
    return result


def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add all required indicators to a 5-min OHLCV DataFrame.
    Columns added:
      atr, atr_avg (20-period Wilder MA of ATR), atr_ratio,
      adx, plus_di, minus_di,
      ema200, rsi, rsi_ma5
    """
    df = df.copy()
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # ── True Range
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    tr.iloc[0] = high.iloc[0] - low.iloc[0]

    # ── ATR-14 (Wilder)
    atr14 = tr.rolling(window=14, min_periods=1).mean()  # init with SMA
    atr_wilder = atr14.copy()
    alpha = 1.0 / 14
    for i in range(1, len(atr_wilder)):
        atr_wilder.iloc[i] = atr_wilder.iloc[i - 1] * (1 - alpha) + tr.iloc[i] * alpha
    df["atr"] = atr_wilder

    # ── ATR rolling mean (20-bar) for squeeze detection
    df["atr_avg"] = df["atr"].rolling(20, min_periods=5).mean()
    df["atr_ratio"] = df["atr"] / df["atr_avg"].replace(0, np.nan)

    # ── Directional Movement
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm_s = pd.Series(plus_dm, index=df.index, dtype=float)
    minus_dm_s = pd.Series(minus_dm, index=df.index, dtype=float)

    # Wilder smooth DM and TR for DI calc
    period = 14
    alpha_di = 1.0 / period
    plus_dm_w = plus_dm_s.rolling(period, min_periods=period).mean()
    minus_dm_w = minus_dm_s.rolling(period, min_periods=period).mean()
    atr_di_w = tr.rolling(period, min_periods=period).mean()

    for i in range(period, len(df)):
        plus_dm_w.iloc[i] = plus_dm_w.iloc[i - 1] * (1 - alpha_di) + plus_dm_s.iloc[i] * alpha_di
        minus_dm_w.iloc[i] = minus_dm_w.iloc[i - 1] * (1 - alpha_di) + minus_dm_s.iloc[i] * alpha_di
        atr_di_w.iloc[i] = atr_di_w.iloc[i - 1] * (1 - alpha_di) + tr.iloc[i] * alpha_di

    df["plus_di"] = (plus_dm_w / atr_di_w.replace(0, np.nan)) * 100
    df["minus_di"] = (minus_dm_w / atr_di_w.replace(0, np.nan)) * 100
    df["plus_di"] = df["plus_di"].fillna(0.0)
    df["minus_di"] = df["minus_di"].fillna(0.0)

    # ── ADX
    dx = (
        (df["plus_di"] - df["minus_di"]).abs()
        / (df["plus_di"] + df["minus_di"]).replace(0, np.nan)
        * 100
    ).fillna(0.0)
    adx = dx.rolling(period, min_periods=period).mean()
    for i in range(period * 2, len(df)):
        adx.iloc[i] = adx.iloc[i - 1] * (1 - alpha_di) + dx.iloc[i] * alpha_di
    df["adx"] = adx.fillna(0.0)

    # ── EMA-200
    df["ema200"] = _ema(close, 200)

    # ── RSI-14
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = (100 - 100 / (1 + rs)).fillna(50.0)
    df["rsi_ma5"] = df["rsi"].rolling(5, min_periods=1).mean()

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Core: detect_and_label
# ─────────────────────────────────────────────────────────────────────────────

def detect_and_label(
    df_5m: pd.DataFrame,
    features_df: pd.DataFrame,
    instrument: str,
    cfg: dict,
) -> pd.DataFrame:
    """
    Run Mode-A squeeze-breakout signal detection and exit simulation on a single
    instrument's 5-min OHLCV data, join features at the entry bar, and return
    a DataFrame of labeled trades.

    Parameters
    ----------
    df_5m : pd.DataFrame
        5-min OHLCV data with a DatetimeIndex or a 'datetime' column.
        Required columns: open, high, low, close, volume
    features_df : pd.DataFrame
        Feature data indexed on 'datetime' (from Stage 2).
        Must contain columns starting with 'f_'.
    instrument : str
        Instrument name (e.g. 'TMF', 'BTCUSDT').
    cfg : dict
        Instrument config from INSTRUMENT_CONFIGS.

    Returns
    -------
    pd.DataFrame
        One row per detected trade, with label columns and joined features.
        Empty DataFrame if fewer than 1 trade is found.
    """
    # ── Normalise index
    df = df_5m.copy()
    if "datetime" in df.columns:
        df = df.set_index("datetime")
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    if len(df) < 250:
        print(f"  [{instrument}] Insufficient rows ({len(df)}), skipping.")
        return pd.DataFrame()

    # ── Compute indicators
    df = _compute_indicators(df)

    # ── Extract config
    sess_start: time = cfg["session_start"]
    sess_end: time = cfg["session_end"]
    afternoon_t: time = cfg["afternoon_time"]
    min_adx: float = cfg["min_adx"]
    afternoon_min_adx: float = cfg["afternoon_min_adx"]
    squeeze_ratio: float = cfg["squeeze_ratio"]
    expand_ratio: float = cfg["expand_ratio"]
    squeeze_grace_bars: int = cfg["squeeze_grace_bars"]
    sl_atr: float = cfg["sl_atr"]
    tp_atr: float = cfg["tp_atr"]
    trail_trigger_atr: float = cfg["trail_trigger_atr"]
    trail_dist_atr: float = cfg["trail_dist_atr"]
    max_bars: int = cfg["max_bars"]
    early_cut_bars: int = cfg["early_cut_bars"]
    early_cut_loss_atr: float = cfg["early_cut_loss_atr"]
    min_di_gap: float = cfg["min_di_gap"]
    rsi_bull: float = cfg["momentum_rsi_bull"]
    rsi_bear: float = cfg["momentum_rsi_bear"]
    trend_filter: bool = cfg["trend_filter"]

    # ── Prepare features lookup (keyed on bar datetime)
    feat_cols = [c for c in features_df.columns if c.startswith("f_")]
    if "datetime" in features_df.columns:
        feat_lookup = features_df.set_index("datetime")[feat_cols]
    else:
        feat_lookup = features_df[feat_cols]
    feat_lookup.index = pd.to_datetime(feat_lookup.index)

    # ── State variables (per-session, reset on new trading day)
    squeeze_bars: int = 0
    in_grace: bool = False
    grace_count: int = 0
    was_squeeze: bool = False

    # ── Trade tracking
    in_trade: bool = False
    trade_dir: int = 0           # +1 LONG, -1 SHORT
    entry_price: float = 0.0
    stop_price: float = 0.0
    tp_price: float = 0.0
    atr_at_entry: float = 0.0
    entry_bar_idx: int = 0
    bars_held: int = 0
    trail_stop: float = 0.0
    trail_active: bool = False
    entry_time_val = None
    adx_at_entry: float = 0.0
    pdi_at_entry: float = 0.0
    ndi_at_entry: float = 0.0
    squeeze_dur_at_entry: int = 0
    session_trade_done: bool = False
    current_date: Optional[str] = None

    records = []
    n = len(df)
    idx_arr = df.index
    close_arr = df["close"].values
    high_arr = df["high"].values
    low_arr = df["low"].values
    atr_arr = df["atr"].values
    atr_ratio_arr = df["atr_ratio"].values
    adx_arr = df["adx"].values
    plus_di_arr = df["plus_di"].values
    minus_di_arr = df["minus_di"].values
    ema200_arr = df["ema200"].values
    rsi_arr = df["rsi"].values

    warmup = 220  # skip first N bars for indicator stabilisation

    for i in range(warmup, n):
        ts = idx_arr[i]
        bar_time = ts.time()
        bar_date = ts.strftime("%Y-%m-%d")

        # ── New day: reset session state
        if bar_date != current_date:
            current_date = bar_date
            session_trade_done = False
            # Do NOT reset squeeze state across days — let it carry through
            # (This mirrors live strategy behaviour: first bar of day may still
            #  be in grace from prior session, which is acceptable.)

        # ── Session filter
        if not (sess_start <= bar_time <= sess_end):
            # If we're in a trade and the session ended, close it
            if in_trade:
                close_price = close_arr[i - 1]
                pnl_dir = (close_price - entry_price) * trade_dir
                risk = abs(entry_price - stop_price) if abs(entry_price - stop_price) > 0 else atr_at_entry * sl_atr
                r_mult = pnl_dir / risk if risk > 0 else 0.0
                records.append(_make_record(
                    instrument, entry_bar_idx, idx_arr[entry_bar_idx],
                    i - 1, idx_arr[i - 1], trade_dir, entry_price, stop_price,
                    tp_price, close_price, "session_end",
                    r_mult, pnl_dir / entry_price * 100,
                    atr_at_entry, adx_at_entry, pdi_at_entry, ndi_at_entry,
                    squeeze_dur_at_entry,
                ))
                in_trade = False
                session_trade_done = True
            continue

        # ── Only one trade per session
        if session_trade_done and not in_trade:
            # Still process squeeze state updates even when trade is done
            _update_squeeze_state_vars = True
        else:
            _update_squeeze_state_vars = True

        atr_val = atr_arr[i] if not np.isnan(atr_arr[i]) and atr_arr[i] > 0 else 1.0
        atr_ratio = atr_ratio_arr[i] if not np.isnan(atr_ratio_arr[i]) else 1.0
        close_val = close_arr[i]
        adx_val = adx_arr[i]
        plus_di = plus_di_arr[i]
        minus_di = minus_di_arr[i]
        ema200 = ema200_arr[i]
        rsi_val = rsi_arr[i]

        # ── Manage open trade exit
        if in_trade:
            bars_held += 1
            pnl_pts = (close_val - entry_price) * trade_dir
            unrealized = pnl_pts

            exit_price: Optional[float] = None
            exit_reason: str = ""

            # 1. Hard stop: check low/high for stop hit
            if trade_dir == 1:  # LONG
                if low_arr[i] <= stop_price:
                    exit_price = stop_price
                    exit_reason = "stop_loss"
            else:  # SHORT
                if high_arr[i] >= stop_price:
                    exit_price = stop_price
                    exit_reason = "stop_loss"

            # 2. Trail activation and update
            if exit_price is None:
                pnl_atr = unrealized / atr_at_entry if atr_at_entry > 0 else 0.0
                if pnl_atr >= trail_trigger_atr:
                    trail_active = True

                if trail_active:
                    if trade_dir == 1:
                        new_trail = close_val - trail_dist_atr * atr_at_entry
                        trail_stop = max(trail_stop, new_trail)
                        if close_val <= trail_stop:
                            exit_price = trail_stop
                            exit_reason = "trail_stop"
                    else:
                        new_trail = close_val + trail_dist_atr * atr_at_entry
                        trail_stop = min(trail_stop, new_trail)
                        if close_val >= trail_stop:
                            exit_price = trail_stop
                            exit_reason = "trail_stop"

            # 3. Take profit
            if exit_price is None:
                if trade_dir == 1 and high_arr[i] >= tp_price:
                    exit_price = tp_price
                    exit_reason = "take_profit"
                elif trade_dir == -1 and low_arr[i] <= tp_price:
                    exit_price = tp_price
                    exit_reason = "take_profit"

            # 4. Early cut: held too long and still losing
            if exit_price is None and bars_held >= early_cut_bars:
                if unrealized < -early_cut_loss_atr * atr_at_entry:
                    exit_price = close_val
                    exit_reason = "early_cut"

            # 5. Max bars time exit
            if exit_price is None and bars_held >= max_bars:
                exit_price = close_val
                exit_reason = "max_bars"

            if exit_price is not None:
                pnl_dir = (exit_price - entry_price) * trade_dir
                risk = abs(entry_price - stop_price) if abs(entry_price - stop_price) > 0 else atr_at_entry * sl_atr
                r_mult = pnl_dir / risk if risk > 0 else 0.0
                records.append(_make_record(
                    instrument, entry_bar_idx, idx_arr[entry_bar_idx],
                    i, ts, trade_dir, entry_price, stop_price, tp_price,
                    exit_price, exit_reason, r_mult,
                    pnl_dir / entry_price * 100,
                    atr_at_entry, adx_at_entry, pdi_at_entry, ndi_at_entry,
                    squeeze_dur_at_entry,
                ))
                in_trade = False
                session_trade_done = True
                # Reset squeeze state after exit so we can re-enter next session
                was_squeeze = False
                squeeze_bars = 0
                in_grace = False
                grace_count = 0
            continue  # don't attempt new entry on same bar

        # ── Squeeze state machine
        if atr_ratio < squeeze_ratio:
            was_squeeze = True
            squeeze_bars += 1
            in_grace = False
            grace_count = 0
        else:
            if in_grace:
                grace_count += 1
                if grace_count > squeeze_grace_bars:
                    was_squeeze = False
                    in_grace = False
                    grace_count = 0
            elif squeeze_bars >= 3:
                # Valid squeeze ended — enter grace period
                in_grace = True
                grace_count = 1
                if grace_count > squeeze_grace_bars:
                    was_squeeze = False
                    in_grace = False
                    grace_count = 0
            else:
                # Not enough squeeze bars
                was_squeeze = False
                grace_count = 0
            squeeze_bars = 0

        # ── Skip entry if already traded this session
        if session_trade_done:
            continue

        # ── ADX threshold (afternoon tightening)
        effective_min_adx = min_adx
        if bar_time >= afternoon_t:
            effective_min_adx = max(min_adx, afternoon_min_adx)

        # ── Entry conditions
        if not (was_squeeze and atr_ratio >= expand_ratio and adx_val >= effective_min_adx):
            continue

        di_gap = plus_di - minus_di
        direction = 1 if di_gap > 0 else -1  # LONG if +DI dominates

        if abs(di_gap) < min_di_gap:
            continue

        # ── Momentum filter (RSI)
        if direction == 1 and rsi_val < rsi_bull:
            continue
        if direction == -1 and rsi_val > rsi_bear:
            continue

        # ── Trend filter (EMA200)
        if trend_filter and ema200 > 0:
            if direction == 1 and close_val < ema200:
                continue
            if direction == -1 and close_val > ema200:
                continue

        # ── Entry confirmed
        entry_price = close_val
        atr_at_entry = atr_val
        stop_price = entry_price - direction * sl_atr * atr_val
        tp_price = entry_price + direction * tp_atr * atr_val
        trail_stop = stop_price  # initialise trail at stop
        trail_active = False
        bars_held = 0
        trade_dir = direction
        entry_bar_idx = i
        entry_time_val = ts
        adx_at_entry = adx_val
        pdi_at_entry = plus_di
        ndi_at_entry = minus_di
        squeeze_dur_at_entry = squeeze_bars  # bars prior to expansion
        in_trade = True

        # Reset squeeze flags
        was_squeeze = False
        squeeze_bars = 0
        in_grace = False
        grace_count = 0

    # ── Close any trade still open at end of data
    if in_trade and n > 0:
        last_i = n - 1
        close_price = close_arr[last_i]
        pnl_dir = (close_price - entry_price) * trade_dir
        risk = abs(entry_price - stop_price) if abs(entry_price - stop_price) > 0 else atr_at_entry * sl_atr
        r_mult = pnl_dir / risk if risk > 0 else 0.0
        records.append(_make_record(
            instrument, entry_bar_idx, idx_arr[entry_bar_idx],
            last_i, idx_arr[last_i], trade_dir, entry_price, stop_price,
            tp_price, close_price, "end_of_data",
            r_mult, pnl_dir / entry_price * 100,
            atr_at_entry, adx_at_entry, pdi_at_entry, ndi_at_entry,
            squeeze_dur_at_entry,
        ))

    if not records:
        print(f"  [{instrument}] No trades detected.")
        return pd.DataFrame()

    result_df = pd.DataFrame(records)

    # ── Join features at entry bar
    result_df = _join_features(result_df, feat_lookup)

    # ── Add direction-adjusted features
    result_df = add_direction_features(result_df)

    return result_df


def _make_record(
    instrument: str,
    entry_bar: int,
    entry_time,
    exit_bar: int,
    exit_time,
    direction: int,
    entry_price: float,
    stop_price: float,
    tp_price: float,
    exit_price: float,
    exit_reason: str,
    r_multiple: float,
    pnl_pct: float,
    atr_at_entry: float,
    adx_at_entry: float,
    pdi_at_entry: float,
    ndi_at_entry: float,
    squeeze_dur: int,
) -> dict:
    return {
        "instrument": instrument,
        "entry_bar": entry_bar,
        "entry_time": pd.Timestamp(entry_time),
        "exit_bar": exit_bar,
        "exit_time": pd.Timestamp(exit_time),
        "direction": direction,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "tp_price": tp_price,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "r_multiple": r_multiple,
        "win": int(r_multiple > 0),
        "pnl_pct": pnl_pct,
        "atr_at_entry": atr_at_entry,
        "adx_at_entry": adx_at_entry,
        "pdi_at_entry": pdi_at_entry,
        "ndi_at_entry": ndi_at_entry,
        "squeeze_dur": squeeze_dur,
    }


def _join_features(result_df: pd.DataFrame, feat_lookup: pd.DataFrame) -> pd.DataFrame:
    """
    Left-join feature columns onto the trade DataFrame using entry_time as
    the key.  Feature timestamps are matched with a tolerance of ±5 minutes
    using merge_asof so that minor timestamp misalignments don't silently
    drop rows.
    """
    if feat_lookup.empty or result_df.empty:
        return result_df

    feat_cols = feat_lookup.columns.tolist()
    feat_reset = feat_lookup.reset_index().rename(columns={"index": "entry_time", feat_lookup.index.name: "entry_time"})
    feat_reset["entry_time"] = pd.to_datetime(feat_reset["entry_time"])
    feat_reset = feat_reset.sort_values("entry_time").reset_index(drop=True)

    trades_sorted = result_df.sort_values("entry_time").reset_index(drop=True)

    merged = pd.merge_asof(
        trades_sorted,
        feat_reset,
        on="entry_time",
        tolerance=pd.Timedelta("5min"),
        direction="nearest",
    )

    # Fill missing features with NaN (graceful degradation)
    for col in feat_cols:
        if col not in merged.columns:
            merged[col] = np.nan

    return merged


def add_direction_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add direction-adjusted fd_* features. These flip the sign for SHORT trades
    so that 'favorable direction' always = positive value."""
    d = df['direction'].values.astype(float)  # +1 or -1

    # RSI: center at 50, flip for SHORT
    if 'f_rsi' in df.columns:
        df['fd_rsi']   = (df['f_rsi']   - 50) * d
    if 'f_rsi7' in df.columns:
        df['fd_rsi7']  = (df['f_rsi7']  - 50) * d
    if 'f_rsi21' in df.columns:
        df['fd_rsi21'] = (df['f_rsi21'] - 50) * d
    if 'f_rsi_slope' in df.columns:
        df['fd_rsi_slope'] = df['f_rsi_slope'] * d
    if 'f_rsi_vs_50' in df.columns:
        df['fd_rsi_vs50'] = df['f_rsi_vs_50'] * d

    # DI spread: already (+DI) - (-DI), flip for SHORT
    if 'f_di_spread' in df.columns:
        df['fd_di_spread'] = df['f_di_spread'] * d
    if 'f_di_spread_abs' in df.columns:
        df['fd_di_spread_abs'] = df['f_di_spread_abs']  # abs is direction-neutral

    # EMA trend: flip for SHORT
    for col in ['f_ema5_vs_ema20', 'f_ema20_vs_ema60', 'f_ema20_vs_ema200',
                'f_close_vs_ema200', 'f_close_vs_ema20', 'f_ema5_slope', 'f_ema20_slope']:
        if col in df.columns:
            df[col.replace('f_', 'fd_')] = df[col] * d

    # Returns: flip for SHORT
    for col in ['f_ret1', 'f_ret3', 'f_ret5', 'f_ret10', 'f_ret20', 'f_ret_vs_atr']:
        if col in df.columns:
            df[col.replace('f_', 'fd_')] = df[col] * d

    # BB position: center at 0.5, flip for SHORT
    if 'f_bb_pos' in df.columns:
        df['fd_bb_pos'] = (df['f_bb_pos'] - 0.5) * d

    # MACD: flip for SHORT (positive MACD is bullish)
    for col in ['f_macd_hist', 'f_macd_signal', 'f_macd_slope']:
        if col in df.columns:
            df[col.replace('f_', 'fd_')] = df[col] * d

    # Stochastic: center at 50, flip for SHORT
    for col in ['f_stoch_k', 'f_stoch_d']:
        if col in df.columns:
            df[col.replace('f_', 'fd_')] = (df[col] - 50) * d

    # Williams %R: ranges -100 to 0; center at -50, flip for SHORT
    if 'f_williams_r' in df.columns:
        df['fd_williams_r'] = (df['f_williams_r'] + 50) * d

    # CCI: flip for SHORT (positive CCI = bullish)
    for col in ['f_cci14', 'f_cci20']:
        if col in df.columns:
            df[col.replace('f_', 'fd_')] = df[col] * d

    # Price proximity: direction-sensitive
    if 'f_recent_high_dist' in df.columns and 'f_recent_low_dist' in df.columns:
        # For LONG: we want far from recent high (room to run), close to recent low (support)
        # For SHORT: opposite
        df['fd_favorable_dist'] = (df['f_recent_low_dist'] * d.clip(0, 1) +
                                    df['f_recent_high_dist'] * (-d).clip(0, 1))

    # Multi-timeframe RSI
    for col in ['f_rsi_vs50_15m']:
        if col in df.columns:
            df[col.replace('f_', 'fd_')] = df[col] * d

    # Is long flag
    df['f_is_long'] = (d > 0).astype('float32')

    # Market-type flag: 0 = index/futures, 1 = crypto
    INDEX_INSTRUMENTS = {'TMF', 'TX', 'SPXL', 'TECL', 'QQQM', 'IVV'}
    if 'instrument' in df.columns:
        df['f_market_type'] = df['instrument'].apply(
            lambda x: 0.0 if x in INDEX_INSTRUMENTS else 1.0
        ).astype('float32')
    else:
        df['f_market_type'] = 1.0  # default crypto if unknown

    return df


# ─────────────────────────────────────────────────────────────────────────────
# build_dataset — process all instruments and pool
# ─────────────────────────────────────────────────────────────────────────────

def build_dataset(data_dir: Path, min_trades: int = 5) -> pd.DataFrame:
    """
    Process all configured instruments, detect signals, label trades, and
    save per-instrument and pooled parquet files.

    Outputs (written to data_dir):
      {instrument}_signals.parquet   — labeled trades per instrument
      ml_dataset.parquet             — pooled dataset across all instruments

    Parameters
    ----------
    data_dir : Path
        Directory containing {instrument}_5m.parquet and
        {instrument}_features.parquet files.
    min_trades : int
        Minimum number of trades required to include an instrument in the
        pooled dataset (default 5).

    Returns
    -------
    pd.DataFrame
        The pooled labeled dataset.
    """
    data_dir = Path(data_dir)
    if not data_dir.exists():
        print(f"[ERROR] Data directory not found: {data_dir}")
        return pd.DataFrame()

    all_dfs = []
    skipped = []

    for instrument, cfg in INSTRUMENT_CONFIGS.items():
        print(f"\n[Stage 3] Processing {instrument} ...")

        price_path = data_dir / f"{instrument}_5m.parquet"
        feat_path = data_dir / f"{instrument}_features.parquet"

        if not price_path.exists():
            print(f"  [{instrument}] Price file not found: {price_path}  — skipping.")
            skipped.append(instrument)
            continue

        # ── Load price data
        try:
            df_5m = pd.read_parquet(price_path)
        except Exception as exc:
            print(f"  [{instrument}] Failed to load price data: {exc}  — skipping.")
            skipped.append(instrument)
            continue

        required_cols = {"open", "high", "low", "close", "volume"}
        missing = required_cols - set(df_5m.columns)
        if missing:
            print(f"  [{instrument}] Missing columns {missing}  — skipping.")
            skipped.append(instrument)
            continue

        print(f"  [{instrument}] Loaded {len(df_5m):,} bars from {price_path.name}")

        # ── Load features (optional — trades still labeled without features)
        features_df = pd.DataFrame()
        if feat_path.exists():
            try:
                features_df = pd.read_parquet(feat_path)
                feat_cols = [c for c in features_df.columns if c.startswith("f_")]
                print(f"  [{instrument}] Loaded {len(features_df):,} feature rows, "
                      f"{len(feat_cols)} f_* columns.")
            except Exception as exc:
                print(f"  [{instrument}] Warning: could not load features: {exc}")
        else:
            print(f"  [{instrument}] Features file not found ({feat_path.name}), "
                  "proceeding without features.")

        # ── Detect and label
        labeled = detect_and_label(df_5m, features_df, instrument, cfg)

        if labeled.empty:
            print(f"  [{instrument}] No trades labeled — skipping.")
            skipped.append(instrument)
            continue

        n_trades = len(labeled)
        n_wins = labeled["win"].sum()
        win_rate = n_wins / n_trades * 100
        avg_r = labeled["r_multiple"].mean()
        print(
            f"  [{instrument}] {n_trades} trades | "
            f"win_rate={win_rate:.1f}% | avg_R={avg_r:.2f}"
        )

        if n_trades < min_trades:
            print(f"  [{instrument}] Too few trades ({n_trades} < {min_trades}) — skipping pool.")
            skipped.append(instrument)
            # Still save per-instrument file for inspection
            out_path = data_dir / f"{instrument}_signals.parquet"
            labeled.to_parquet(out_path, index=False)
            print(f"  [{instrument}] Saved (excluded from pool): {out_path.name}")
            continue

        # ── Save per-instrument file
        out_path = data_dir / f"{instrument}_signals.parquet"
        labeled.to_parquet(out_path, index=False)
        print(f"  [{instrument}] Saved: {out_path.name}")

        all_dfs.append(labeled)

    if not all_dfs:
        print("\n[Stage 3] No instruments produced enough trades. ml_dataset.parquet not written.")
        if skipped:
            print(f"  Skipped instruments: {skipped}")
        return pd.DataFrame()

    # ── Pool and save
    pooled = pd.concat(all_dfs, ignore_index=True)
    pooled = pooled.sort_values("entry_time").reset_index(drop=True)

    # ── Apply direction-adjusted features to pooled dataset
    pooled = add_direction_features(pooled)

    pool_path = data_dir / "ml_dataset.parquet"
    pooled.to_parquet(pool_path, index=False)

    print(f"\n[Stage 3] Pooled dataset: {len(pooled):,} trades across "
          f"{pooled['instrument'].nunique()} instruments")
    print(f"  Overall win_rate={pooled['win'].mean() * 100:.1f}%  "
          f"avg_R={pooled['r_multiple'].mean():.2f}")
    print(f"  Saved: {pool_path}")

    if skipped:
        print(f"  Instruments skipped/excluded: {skipped}")

    # ── Summary statistics per exit reason
    reason_summary = (
        pooled.groupby("exit_reason")
        .agg(count=("win", "count"), win_rate=("win", "mean"), avg_r=("r_multiple", "mean"))
        .round(3)
    )
    print("\n  Exit reason breakdown:")
    print(reason_summary.to_string())

    return pooled


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Stage 3: TMF Breakout — Signal Detection + Labeling")
    print("=" * 60)
    result = build_dataset(ML_DATA_DIR)
    if result.empty:
        print("\n[Stage 3] Finished with no output.")
    else:
        print(f"\n[Stage 3] Done. {len(result):,} labeled trades ready for ML.")
