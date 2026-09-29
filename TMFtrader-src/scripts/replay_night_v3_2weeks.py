"""night_v3(60m,被 lab 判死的舊版)2026-05-28~06-05 兩週績效對照。
同 replay_night_v7_2weeks.py 口徑,只差 TF=60m / or_bars=8 / min_adx=25 / max_hold=24。"""
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

TF = "60min"
h = pd.read_parquet(ROOT / "data" / "history" / "TMFR1_1min_202605.parquet")
h["ts"] = pd.to_datetime(h["ts"])
h = h.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
h = h[(h["ts"] >= "2026-05-20") & (h["ts"] < "2026-06-01")].set_index("ts")
hb = pd.DataFrame({"open": h["open"].resample(TF).first(), "high": h["high"].resample(TF).max(),
                   "low": h["low"].resample(TF).min(), "close": h["close"].resample(TF).last(),
                   "volume": h["volume"].resample(TF).sum()}).dropna(subset=["open"])
files = sorted(glob.glob(str(ROOT / "data" / "ticks" / "TMF_2026060[1-6].csv")))
ticks = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
ticks["ts"] = pd.to_datetime(ticks["ts"]); ticks = ticks.sort_values("ts").set_index("ts")
tb = ticks["price"].resample(TF).ohlc(); tb["volume"] = ticks["volume"].resample(TF).sum()
tb = tb.dropna(subset=["open"])
bars = pd.concat([hb, tb]).reset_index()
bars.columns = ["datetime", "open", "high", "low", "close", "volume"]
bars = bars.sort_values("datetime").reset_index(drop=True)
print(f"60m bars: {len(bars)} | {bars['datetime'].min()} ~ {bars['datetime'].max()}")


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


adx_real = wilder_adx(bars)
# 診斷 06-05 夜盤:每根 close + 真 ADX + 是否破 OR_lo(44201)-buffer
print("\n=== 診斷 06-05 夜盤(60m):close / ADX / 破不破 OR_lo-buffer ===")
or_lo = 44201; buf = 0.35  # OR=[44201,45315] ATR~322 → 門檻 44201-113=44088
m = (bars.datetime >= "2026-06-05 22:00") & (bars.datetime <= "2026-06-06 05:00")
for i in bars.index[m]:
    r = bars.loc[i]
    thr = or_lo - buf * 322
    flag = "← 破!做空條件" if r["close"] < thr else ""
    print(f"  {r['datetime']:%m-%d %H:%M} C{r['close']:.0f} ADX={adx_real[i]:.1f} (門檻{thr:.0f}) {flag}")

FROZEN = dict(mode="breakout", or_bars=8, buf_atr=0.35, max_or_atr=6.5, sl_atr=1.0,
              tp_atr=4.0, trail_trigger_atr=1.0, trail_dist_atr=1.2, max_hold_bars=24,
              min_adx=25.0, min_or_atr=2.4, max_loss_twd=4000.0, point_value=10.0)
ind = precompute_all(bars, verbose=False)
ind["adx"] = adx_real
res = FastBacktestEngine(initial_balance=222890, instrument="TMF",
                         intrabar_hard_exits=True, slippage=1).run(bars, ind, NightORBStrategy(**FROZEN), "tmf_3x")
trades = [t for t in res.trades if str(t.get("entry_time", "")) >= "2026-05-28" and str(t.get("entry_time", "")) < "2026-06-06"]
print(f"\n=== night_v3 績效 2026-05-28~06-05({len(trades)} 筆)===")
net = 0
for t in trades:
    pnl = t.get("net_pnl", t.get("pnl", 0)); net += pnl
    print(f"  進{str(t.get('entry_time',''))[5:16]} {t.get('side','')} @{t.get('entry_price',0):.0f} → 出{str(t.get('exit_time',''))[5:16]} @{t.get('exit_price',0):.0f} | {pnl:+.0f} | {t.get('reason','')}")
if trades:
    wins = sum(1 for t in trades if t.get("net_pnl", t.get("pnl", 0)) > 0)
    print(f"\n  合計:{len(trades)}筆 淨{net:+.0f}元 勝{wins}/{len(trades)}")
else:
    print("  ❌ 兩週內無進場")
