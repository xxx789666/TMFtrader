#!/usr/bin/env python3
"""Resample recorded tick CSVs (data/ticks/{SYM}_YYYYMMDD.csv) into 1-min OHLCV
parquet matching the shioaji history schema (ts,Open,High,Low,Close,Volume,Amount).

Faithful to shioaji kbar convention, derived from existing TMFR1_1min_202605.parquet:
  - bar timestamp = RIGHT edge (bar labeled T covers trades in [T-1min, T))
    -> resample(label='right', closed='left')
  - only traded ticks (volume>0); volume-0 quote/snapshot rows dropped
  - only minutes inside real trading sessions; pre-open 試撮 ticks dropped via
    label minute-of-day filter (day 08:46..13:46 ; night 15:01..05:01 wrap):
      session gaps in May parquet: 05:01->08:46 and 13:46->15:01
"""
import sys, glob, os
import pandas as pd

TICK_DIR = sys.argv[1] if len(sys.argv) > 1 else "data/ticks"
OUT_DIR  = sys.argv[2] if len(sys.argv) > 2 else "/tmp/resampled_history"
os.makedirs(OUT_DIR, exist_ok=True)

# SYM (in tick filename) -> contract code (in history filename)
SYM2CONTRACT = {"TMF": "TMFR1", "MXF": "MXFR1", "TXF": "TXFR1"}

def mod(ts):  # minute-of-day of a Series of timestamps
    return ts.dt.hour * 60 + ts.dt.minute

def in_session(label_ts):
    m = mod(label_ts)
    day   = (m >= 526) & (m <= 826)            # 08:46 .. 13:46
    night = (m >= 901) | (m <= 301)            # 15:01 .. 05:01 (wraps midnight)
    return day | night

def resample_one(sym, csv_paths):
    frames = []
    for p in sorted(csv_paths):
        d = pd.read_csv(p)
        frames.append(d)
    raw = pd.concat(frames, ignore_index=True)
    raw["ts"] = pd.to_datetime(raw["ts"])
    trades = raw[raw["volume"] > 0].copy()        # drop quote/snapshot rows
    trades["amt"] = trades["price"] * trades["volume"]
    g = trades.set_index("ts").resample("1min", label="right", closed="left")
    bar = pd.DataFrame({
        "Open":   g["price"].first(),
        "High":   g["price"].max(),
        "Low":    g["price"].min(),
        "Close":  g["price"].last(),
        "Volume": g["volume"].sum(),
        "Amount": g["amt"].sum(),
    }).dropna(subset=["Open"])                     # drop empty minutes (no trades)
    bar = bar[in_session(pd.Series(bar.index, index=bar.index))]
    bar["Volume"] = bar["Volume"].astype("int64")
    bar = bar.reset_index().rename(columns={"ts": "ts"})
    bar = bar[["ts", "Open", "High", "Low", "Close", "Volume", "Amount"]]
    return bar

def main():
    syms = {}
    for p in glob.glob(os.path.join(TICK_DIR, "*.csv")):
        base = os.path.basename(p)            # e.g. TMF_20260601.csv
        sym = base.split("_")[0]
        syms.setdefault(sym, []).append(p)

    for sym, paths in sorted(syms.items()):
        if sym not in SYM2CONTRACT:
            print(f"[skip] unknown sym {sym}")
            continue
        contract = SYM2CONTRACT[sym]
        bar = resample_one(sym, paths)
        if bar.empty:
            print(f"[warn] {sym}: no bars produced")
            continue
        # split by year-month, write {contract}_1min_{YYYYMM}.parquet
        bar["ym"] = bar["ts"].dt.strftime("%Y%m")
        for ym, sub in bar.groupby("ym"):
            sub = sub.drop(columns="ym").reset_index(drop=True)
            out = os.path.join(OUT_DIR, f"{contract}_1min_{ym}.parquet")
            sub.to_parquet(out, index=False)
            d0, d1 = sub["ts"].min(), sub["ts"].max()
            days = sub["ts"].dt.normalize().nunique()
            print(f"[ok] {out}  rows={len(sub)}  days={days}  {d0} -> {d1}")
            # spot-check a day-open bar (expect first bar label 08:46)
            opens = sub[sub["ts"].dt.strftime('%H:%M') == '08:46']
            if len(opens):
                r = opens.iloc[0]
                print(f"      day-open sample {r['ts']}  O={r['Open']} H={r['High']} "
                      f"L={r['Low']} C={r['Close']} V={r['Volume']}")

if __name__ == "__main__":
    main()
