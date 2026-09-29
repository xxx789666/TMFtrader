"""night_v7(30m)2026-05-28~06-05 兩週績效。
資料:history 1分K(TMFR1_1min_202605,涵蓋到 05-30 05:00)接 tick(06-01~06-06)→ 30m bar。
warm-up 從 05-20 起;ADX 用手算 Wilder(避開 precompute 在 gappy bar 的 ADX=0)。
night_v7 凍結:30m/or_bars11/min_adx30/sl1.0/max_hold18/全夜盤;tmf_3x、slip1、本金 222,890。"""
import sys, glob
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from core.logger import setup_logger; setup_logger(console_level="INFO")
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.night_orb import NightORBStrategy


def r30(idx_price_df):
    return None


# 1. history 1分K → 30m(warm-up 從 05-20)
h = pd.read_parquet(ROOT / "data" / "history" / "TMFR1_1min_202605.parquet")
h["ts"] = pd.to_datetime(h["ts"])
h = h.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
h = h[(h["ts"] >= "2026-05-20") & (h["ts"] < "2026-06-01")].set_index("ts")
h30 = pd.DataFrame({
    "open": h["open"].resample("30min").first(), "high": h["high"].resample("30min").max(),
    "low": h["low"].resample("30min").min(), "close": h["close"].resample("30min").last(),
    "volume": h["volume"].resample("30min").sum()}).dropna(subset=["open"])

# 2. ticks 06-01~06-06 → 30m
files = sorted(glob.glob(str(ROOT / "data" / "ticks" / "TMF_2026060[1-6].csv")))
ticks = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
ticks["ts"] = pd.to_datetime(ticks["ts"]); ticks = ticks.sort_values("ts").set_index("ts")
t30 = ticks["price"].resample("30min").ohlc()
t30["volume"] = ticks["volume"].resample("30min").sum()
t30 = t30.dropna(subset=["open"])

bars = pd.concat([h30, t30]).reset_index()
bars.columns = ["datetime", "open", "high", "low", "close", "volume"]
bars = bars.sort_values("datetime").reset_index(drop=True)
print(f"30m bars: {len(bars)} | {bars['datetime'].min()} ~ {bars['datetime'].max()}（history 接 tick）")


def wilder_adx(df, period=14):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    n = len(c); tr = np.zeros(n); pdm = np.zeros(n); mdm = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
    def sm(x):
        s = np.zeros(n)
        if n > period:
            s[period] = x[1:period + 1].sum()
            for i in range(period + 1, n):
                s[i] = s[i - 1] - s[i - 1] / period + x[i]
        return s
    atr, sp, smd = sm(tr), sm(pdm), sm(mdm); dx = np.zeros(n); adx = np.zeros(n)
    for i in range(period, n):
        if atr[i] > 0:
            p = 100 * sp[i] / atr[i]; m = 100 * smd[i] / atr[i]
            dx[i] = 100 * abs(p - m) / (p + m) if (p + m) > 0 else 0
    if n > 2 * period:
        adx[2 * period] = dx[period + 1:2 * period + 1].mean()
        for i in range(2 * period + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return adx


FROZEN = dict(mode="breakout", or_bars=11, buf_atr=0.35, max_or_atr=6.5, sl_atr=1.0,
              tp_atr=4.0, trail_trigger_atr=1.0, trail_dist_atr=1.2, max_hold_bars=18,
              min_adx=30.0, min_or_atr=2.4, max_loss_twd=4000.0, point_value=10.0)
ind = precompute_all(bars, verbose=False)
ind["adx"] = wilder_adx(bars)
res = FastBacktestEngine(initial_balance=222890, instrument="TMF",
                         intrabar_hard_exits=True, slippage=1).run(bars, ind, NightORBStrategy(**FROZEN), "tmf_3x")

# 報告 5/28~6/5
trades = [t for t in res.trades if "2026-05-2" in str(t.get("entry_time", "")) or "2026-05-3" in str(t.get("entry_time", "")) or "2026-06-0" in str(t.get("entry_time", ""))]
trades = [t for t in trades if str(t.get("entry_time", "")) >= "2026-05-28"]
print(f"\n=== night_v7 績效 2026-05-28~06-05({len(trades)} 筆)===")
net = 0
for t in trades:
    pnl = t.get("net_pnl", t.get("pnl", 0)); net += pnl
    print(f"  進{str(t.get('entry_time',''))[5:16]} {t.get('side','')} @{t.get('entry_price',0):.0f} → 出{str(t.get('exit_time',''))[5:16]} @{t.get('exit_price',0):.0f} | {pnl:+.0f} | {t.get('reason','')}")
if trades:
    wins = sum(1 for t in trades if t.get("net_pnl", t.get("pnl", 0)) > 0)
    print(f"\n  合計:{len(trades)}筆 淨{net:+.0f}元 勝{wins}/{len(trades)}")
else:
    print("  ❌ 兩週內無進場(OR 太窄/ADX 不足/無突破)")
