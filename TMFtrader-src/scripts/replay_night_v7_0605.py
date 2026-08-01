"""Replay night_v7(NightORBStrategy 30m 凍結)在 2026-06-05 夜盤。
night_v7:TF=30m / or_bars=11(OR 15:00-20:30)/ min_adx=30 / sl1.0 / max_hold18 / 全夜盤。
用 v7 錄製真實 TMF tick(06-01~06-06)→ 30m bar → FastBacktestEngine。
回答:30m 版會進在幾點、賺多少。口徑:intrabar 硬停損、tmf_3x、slip1、本金 222,890。
ADX 用 Wilder 手算(避開 precompute 在 gappy bar 上的 ADX=0 假象)。"""
import sys, glob
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import warnings; warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from core.logger import setup_logger; setup_logger(console_level="INFO")
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.night_orb import NightORBStrategy

files = sorted(glob.glob(str(ROOT / "data" / "ticks" / "TMF_2026060[1-6].csv")))
print("tick 檔:", [Path(f).name for f in files])
ticks = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
ticks["ts"] = pd.to_datetime(ticks["ts"])
ticks = ticks.sort_values("ts").set_index("ts")
o = ticks["price"].resample("30min").ohlc()
v = ticks["volume"].resample("30min").sum()
bars = o.join(v.rename("volume")).dropna(subset=["open"]).reset_index()
bars.columns = ["datetime", "open", "high", "low", "close", "volume"]
print(f"30m bars: {len(bars)} 根 | {bars['datetime'].min()} ~ {bars['datetime'].max()}")


def wilder_adx(df, period=14):
    """標準 Wilder ADX,把 bar 當連續序列(忽略時間斷層)。回 numpy array。"""
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    n = len(c); tr = np.zeros(n); pdm = np.zeros(n); mdm = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
    def smooth(x):
        s = np.zeros(n)
        if n > period:
            s[period] = x[1:period + 1].sum()
            for i in range(period + 1, n):
                s[i] = s[i - 1] - s[i - 1] / period + x[i]
        return s
    atr, spdm, smdm = smooth(tr), smooth(pdm), smooth(mdm)
    adx = np.zeros(n); dx = np.zeros(n)
    for i in range(period, n):
        if atr[i] > 0:
            pdi = 100 * spdm[i] / atr[i]; mdi = 100 * smdm[i] / atr[i]
            dx[i] = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0
    if n > 2 * period:
        adx[2 * period] = dx[period + 1:2 * period + 1].mean()
        for i in range(2 * period + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return adx


# ── 診斷:06-05 夜盤 30m bars + OR(11 根 = 15:00-20:30) + 手算 ADX ──
night = bars[(bars.datetime >= "2026-06-05 15:00") & (bars.datetime <= "2026-06-06 05:00")].reset_index(drop=True)
adx_real = wilder_adx(bars)
print(f"\n=== 06-05 夜盤 30m bars ({len(night)} 根)+ 手算 ADX ===")
for i in bars.index[(bars.datetime >= "2026-06-05 15:00") & (bars.datetime <= "2026-06-06 05:00")]:
    r = bars.loc[i]
    print(f"  {r['datetime']:%m-%d %H:%M}  O{r['open']:.0f} H{r['high']:.0f} L{r['low']:.0f} C{r['close']:.0f}  ADX={adx_real[i]:.1f}")
if len(night) >= 11:
    orw = night.head(11)
    or_hi, or_lo = orw["high"].max(), orw["low"].min()
    print(f"\n11 根 OR(15:00–20:30):hi={or_hi:.0f} lo={or_lo:.0f} 寬={or_hi-or_lo:.0f}（ready @ {orw['datetime'].iloc[-1]:%H:%M}）")
    after = night[night.index > 10]
    print(f"進場窗（20:30 後）最低={after['low'].min():.0f} 最高={after['high'].max():.0f}")

# ── 回放:用手算 ADX 覆寫 precompute 的(後者在 gappy bar 上全 0)──
FROZEN = dict(mode="breakout", or_bars=11, buf_atr=0.35, max_or_atr=6.5, sl_atr=1.0,
              tp_atr=4.0, trail_trigger_atr=1.0, trail_dist_atr=1.2, max_hold_bars=18,
              min_adx=30.0, min_or_atr=2.4, max_loss_twd=4000.0, point_value=10.0)
ind = precompute_all(bars, verbose=False)
print(f"\nprecompute ADX max={np.nanmax(ind['adx']):.1f}（0=工具壞）→ 用手算 Wilder ADX 覆寫(max={np.nanmax(adx_real):.1f})")
ind["adx"] = adx_real   # 覆寫成真實 ADX
res = FastBacktestEngine(initial_balance=222890, instrument="TMF",
                         intrabar_hard_exits=True, slippage=1).run(bars, ind, NightORBStrategy(**FROZEN), "tmf_3x")
print(f"\n=== night_v7(min_adx=30、真 ADX)回放:全期 {len(res.trades)} 筆 ===")
h = [t for t in res.trades if "2026-06-05" in str(t.get("entry_time", "")) or "2026-06-06" in str(t.get("entry_time", ""))]
print("=== 昨晚 06-05 夜盤 ===")
for t in h:
    print("  ✅", t)
if not h:
    print("  ❌ night_v7(min_adx30)昨晚無進場")
