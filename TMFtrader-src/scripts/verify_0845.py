# -*- coding: utf-8 -*-
"""
Verify 2026-04-17 08:45 why no entry signal.
Recalculate ema200, rsi_ma5, trend_allow_long using exact same formulas
as market_data.py / breakout.py.

Usage:
    cd TMFtrader-src
    python scripts/verify_0845.py
"""

import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# Strategy parameters (match engine.py exactly)
EMA200_MARGIN_ATR    = 0.0   # default, not overridden in engine.py
MOMENTUM_RSI_BULL    = 52.0  # engine.py line 99
MOMENTUM_RSI_BEAR    = 46.0  # engine.py line 98
MOMENTUM_SESSION_ATR = 0.5   # default, not overridden in engine.py

TARGET_BAR = datetime(2026, 4, 17, 8, 45, 0)


# --- Indicator functions (copied from market_data.py) ---

def _ema(data, period):
    if len(data) < period:
        return float(np.mean(data))
    alpha = 2.0 / (period + 1)
    ema = data[0]
    for price in data[1:]:
        ema = alpha * price + (1 - alpha) * ema
    return float(ema)

def _rsi_series(close, period=14):
    if len(close) < period + 1:
        return np.array([50.0])
    deltas = np.diff(close)
    gains  = np.where(deltas > 0, deltas, 0.0)
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

def _atr_series(high, low, close, period=14):
    if len(high) < 2:
        return np.array([0.0])
    tr = np.empty(len(high))
    tr[0] = high[0] - low[0]
    for i in range(1, len(high)):
        tr[i] = max(high[i] - low[i],
                    abs(high[i] - close[i - 1]),
                    abs(low[i]  - close[i - 1]))
    atr = np.empty(len(tr))
    atr[0] = tr[0]
    alpha = 1.0 / period
    for i in range(1, len(tr)):
        atr[i] = alpha * tr[i] + (1 - alpha) * atr[i - 1]
    return atr


# --- Fetch 1min kbars from Shioaji ---

def fetch_1min_kbars(days=10):
    import shioaji as sj
    api = sj.Shioaji()
    api.login(
        api_key    = os.environ["SHIOAJI_API_KEY"],
        secret_key = os.environ["SHIOAJI_SECRET_KEY"],
        receive_window=300000,
    )
    try:
        api.activate_ca(
            ca_path   = os.environ.get("SHIOAJI_CA_PATH", ""),
            ca_passwd  = os.environ["SHIOAJI_CA_PASSWORD"],
            person_id  = os.environ["SHIOAJI_PERSON_ID"],
        )
    except Exception as e:
        print("CA failed (ok for quotes): " + str(e))

    contract = api.Contracts.Futures.MXF.MXFR1
    print("Contract: " + str(contract.code) + " (" + str(contract.name) + ")")

    all_bars = []
    end_date = datetime.now()
    batch = 5
    remaining = days

    while remaining > 0:
        fetch = min(batch, remaining)
        start = end_date - timedelta(days=fetch)
        s_str = start.strftime("%Y-%m-%d")
        e_str = end_date.strftime("%Y-%m-%d")
        print("  Fetching " + s_str + " ~ " + e_str + " ...")
        try:
            kbars = api.kbars(contract=contract, start=s_str, end=e_str)
            if kbars and hasattr(kbars, "Close") and len(kbars.Close) > 0:
                for i in range(len(kbars.Close)):
                    raw_ts = kbars.ts[i]
                    if isinstance(raw_ts, (int, float)):
                        epoch_sec = raw_ts / 1e9 if raw_ts > 1e12 else raw_ts
                        ts = datetime.fromtimestamp(epoch_sec)
                    elif hasattr(raw_ts, "to_pydatetime"):
                        ts = raw_ts.to_pydatetime()
                        if ts.tzinfo is not None:
                            ts = ts.astimezone().replace(tzinfo=None)
                    else:
                        ts = raw_ts
                    all_bars.append({
                        "datetime": ts,
                        "open":   float(kbars.Open[i]),
                        "high":   float(kbars.High[i]),
                        "low":    float(kbars.Low[i]),
                        "close":  float(kbars.Close[i]),
                        "volume": int(kbars.Volume[i]),
                    })
                print("    OK: " + str(len(kbars.Close)) + " bars")
        except Exception as e:
            print("    FAIL: " + str(e))
        end_date = start - timedelta(days=1)
        remaining -= fetch

    api.logout()
    return pd.DataFrame(all_bars).sort_values("datetime").reset_index(drop=True)


# --- Main ---

def main():
    print("=" * 60)
    print("Verify 2026-04-17 08:45 trend_allow_long")
    print("=" * 60)

    df1m = fetch_1min_kbars(days=10)
    print("\n1min bars: " + str(len(df1m)) +
          "  range: " + str(df1m["datetime"].iloc[0]) +
          " ~ " + str(df1m["datetime"].iloc[-1]))

    # Resample to 5min
    df1m["datetime"] = pd.to_datetime(df1m["datetime"])
    df1m = df1m.set_index("datetime")
    df5m = df1m.resample("5min", label="left", closed="left").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna(subset=["close"]).reset_index()
    print("5min bars: " + str(len(df5m)))

    # Find target bar
    target_mask = df5m["datetime"] == TARGET_BAR
    if not target_mask.any():
        diffs = (df5m["datetime"] - TARGET_BAR).abs()
        idx = diffs.idxmin()
        print("WARN: exact 08:45 not found, using nearest: " + str(df5m.loc[idx, "datetime"]))
    else:
        idx = df5m[target_mask].index[0]

    print("\nTarget bar index: " + str(idx) + "  time: " + str(df5m.loc[idx, "datetime"]))
    print("Using " + str(idx + 1) + " bars for indicator calculation")

    if idx < 50:
        print("ERROR: not enough data (need >= 200 bars)")
        return

    sub   = df5m.iloc[:idx + 1].copy()
    close = sub["close"].values.astype(float)
    high  = sub["high"].values.astype(float)
    low   = sub["low"].values.astype(float)

    # Calculate indicators (same as market_data.py)
    ema20   = _ema(close, 20)
    ema200  = _ema(close, 200) if len(close) >= 200 else 0.0
    rsi_arr = _rsi_series(close, 14)
    rsi     = rsi_arr[-1] if len(rsi_arr) > 0 else 50.0
    rsi_ma5 = float(np.mean(rsi_arr[-5:])) if len(rsi_arr) >= 5 else rsi
    atr_arr = _atr_series(high, low, close, 14)
    atr     = atr_arr[-1] if len(atr_arr) > 0 else 0.0
    price   = close[-1]

    print("\n--- Indicators at 08:45 ---")
    print("  price   = " + str(round(price, 0)))
    print("  ema20   = " + str(round(ema20, 1)))
    print("  ema200  = " + str(round(ema200, 1)))
    print("  atr     = " + str(round(atr, 1)))
    print("  rsi     = " + str(round(rsi, 1)))
    print("  rsi_ma5 = " + str(round(rsi_ma5, 1)))

    # trend_allow_long calculation (same as breakout.py _compute_trend_allow)
    print("\n--- trend_allow_long calculation ---")
    margin = EMA200_MARGIN_ATR * atr  # = 0.0
    if price > ema200 + margin:
        ema200_vote = 1
        print("  price(" + str(round(price)) + ") > ema200(" + str(round(ema200,1)) + ") -> ema200_vote = +1")
    elif price < ema200 - margin:
        ema200_vote = -1
        print("  price(" + str(round(price)) + ") < ema200(" + str(round(ema200,1)) + ") -> ema200_vote = -1")
    else:
        ema200_vote = 0
        print("  price(" + str(round(price)) + ") ~ ema200(" + str(round(ema200,1)) + ") -> ema200_vote = 0")

    if rsi_ma5 > MOMENTUM_RSI_BULL:
        rsi_vote = 1
        print("  rsi_ma5(" + str(round(rsi_ma5,1)) + ") > " + str(MOMENTUM_RSI_BULL) + " -> rsi_vote = +1")
    elif rsi_ma5 < MOMENTUM_RSI_BEAR:
        rsi_vote = -1
        print("  rsi_ma5(" + str(round(rsi_ma5,1)) + ") < " + str(MOMENTUM_RSI_BEAR) + " -> rsi_vote = -1")
    else:
        rsi_vote = 0
        print("  rsi_ma5(" + str(round(rsi_ma5,1)) + ") in [" + str(MOMENTUM_RSI_BEAR) + ", " + str(MOMENTUM_RSI_BULL) + "] -> rsi_vote = 0")

    # 08:45 = first bar of day session -> session_open = price -> session_move = 0
    session_vote = 0
    print("  08:45 is first bar -> session_move=0 -> session_vote = 0")

    score       = ema200_vote * 2 + rsi_vote + session_vote
    allow_long  = score > -2
    allow_short = score < 2

    print("\n  score = " + str(ema200_vote) + "x2 + " + str(rsi_vote) + " + " + str(session_vote) + " = " + str(score))
    print("  allow_long  = (score > -2) = (" + str(score) + " > -2) = " + str(allow_long))
    print("  allow_short = (score <  2) = (" + str(score) + " <  2) = " + str(allow_short))

    print("\n" + "=" * 60)
    if allow_long:
        print("RESULT: trend_allow_long = True")
        print("  -> trend_allow was NOT the blocker")
        print("  -> Need to check: _was_squeeze, adx, atrR, di_gap conditions")
    else:
        print("RESULT: trend_allow_long = False")
        print("  -> This is why no LONG signal at 08:45!")
        print("  -> ema200_vote=" + str(ema200_vote) + " rsi_vote=" + str(rsi_vote) + " score=" + str(score) + " <= -2")
    print("=" * 60)


if __name__ == "__main__":
    main()
