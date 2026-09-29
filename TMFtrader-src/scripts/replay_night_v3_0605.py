"""Replay night_v3(NightORBStrategy 凍結 or_bars=8)在 2026-06-05 夜盤。
用 v7 錄製的真實 TMF tick(06-01~06-06、前幾天供 ATR/ADX 暖身)→ 60m bar → FastBacktestEngine。
回答:正確配置(or_bars=8、OR 15:00-22:00、進場 23:00 後)下,昨晚大跌能不能進場做空、賺不賺錢。
口徑:intrabar 硬停損、tmf_3x、slip=1、本金 222,890(=昨晚實際權益)。"""
import sys, glob
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import warnings; warnings.filterwarnings("ignore")
import pandas as pd
from core.logger import setup_logger; setup_logger(console_level="INFO")  # 開 NightORB 判決/skip log
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.night_orb import NightORBStrategy

files = sorted(glob.glob(str(ROOT / "data" / "ticks" / "TMF_2026060[1-6].csv")))
print("tick 檔:", [Path(f).name for f in files])
ticks = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
ticks["ts"] = pd.to_datetime(ticks["ts"])
ticks = ticks.sort_values("ts").set_index("ts")
o = ticks["price"].resample("60min").ohlc()
v = ticks["volume"].resample("60min").sum()
bars = o.join(v.rename("volume")).dropna(subset=["open"]).reset_index()
bars.columns = ["datetime", "open", "high", "low", "close", "volume"]
print(f"60m bars: {len(bars)} 根 | {bars['datetime'].min()} ~ {bars['datetime'].max()}")

# ── 診斷:昨晚 06-05 夜盤的 8 根 OR 與進場窗價格 ──
night = bars[(bars.datetime >= "2026-06-05 15:00") & (bars.datetime <= "2026-06-06 05:00")].reset_index(drop=True)
print(f"\n=== 06-05 夜盤 60m bars ({len(night)} 根) ===")
for _, r in night.iterrows():
    print(f"  {r['datetime']:%m-%d %H:%M}  O{r['open']:.0f} H{r['high']:.0f} L{r['low']:.0f} C{r['close']:.0f}")
if len(night) >= 8:
    orw = night.head(8)
    or_hi, or_lo = orw["high"].max(), orw["low"].min()
    after = night[night.index > 7]
    print(f"\n8 根 OR(15:00–22:00):hi={or_hi:.0f} lo={or_lo:.0f} 寬={or_hi-or_lo:.0f}（ready @ {orw['datetime'].iloc[-1]:%H:%M}）")
    if len(after):
        print(f"進場窗（23:00 後）最低={after['low'].min():.0f} 最高={after['high'].max():.0f}")
        print(f"→ 下破做空需 跌破 {or_lo:.0f} − 0.35×ATR;上破做多需 突破 {or_hi:.0f} + 0.35×ATR")

# ── 真實策略回放 ──
FROZEN = dict(mode="breakout", or_bars=8, buf_atr=0.35, max_or_atr=6.5, sl_atr=1.0,
              trail_trigger_atr=1.0, trail_dist_atr=1.2, max_hold_bars=24,
              min_adx=25.0, min_or_atr=2.4, max_loss_twd=4000.0, point_value=10.0)
ind = precompute_all(bars, verbose=False)
print("\n=== 指標 keys ===", list(ind.keys()))
# 印 06-05 夜盤每根 ATR/ADX(判斷 width/ADX gate)
_atr = ind.get("atr"); _adx = ind.get("adx")
mask = (bars.datetime >= "2026-06-05 15:00") & (bars.datetime <= "2026-06-06 05:00")
print("=== 06-05 夜盤每根 ATR/ADX(min_adx=25、min_or2.4/max_or6.5 × ATR)===")
for i in bars.index[mask]:
    a = _atr[i] if _atr is not None else float("nan")
    d = _adx[i] if _adx is not None else float("nan")
    print(f"  {bars.datetime[i]:%m-%d %H:%M}  ATR={a:.0f}  ADX={d:.1f}  C={bars.close[i]:.0f}")
res = FastBacktestEngine(initial_balance=222890, instrument="TMF",
                         intrabar_hard_exits=True, slippage=1).run(bars, ind, NightORBStrategy(**FROZEN), "tmf_3x")
print(f"\n=== NightORBStrategy 回放結果(全 {len(bars['datetime'].dt.date.unique())} 天、{len(res.trades)} 筆)===")
for t in res.trades:
    print(" ", t)
print("\n=== 昨晚(06-05 夜盤)有無進場 ===")
hit = [t for t in res.trades if "2026-06-05" in str(t.get("entry_time", "")) or "2026-06-06" in str(t.get("entry_time", ""))]
if hit:
    for t in hit:
        print("  ✅ 有進場:", t)
else:
    print("  ❌ 含 ADX 閘 → 無進場(但 replay ADX 全 0 是假象、見下)")

import numpy as np
print(f"\n=== [證明 ADX 假象] 全期 ADX max={np.nanmax(_adx):.1f} mean={np.nanmean(_adx):.1f}（全 0=工具壞、非真實）===")

print("\n=== [繞過壞 ADX 閘:min_adx=0] OR 突破的實際進出場/損益 ===")
print("   (live 昨晚 22:00 真進場 → ADX 當時 >25;此處 min_adx=0 只為避開 replay 的 ADX=0 假象)")
res2 = FastBacktestEngine(initial_balance=222890, instrument="TMF",
                          intrabar_hard_exits=True, slippage=1).run(
    bars, ind, NightORBStrategy(**{**FROZEN, "min_adx": 0.0}), "tmf_3x")
h2 = [t for t in res2.trades if "2026-06-05" in str(t.get("entry_time", "")) or "2026-06-06" in str(t.get("entry_time", ""))]
for t in h2:
    print("  ", t)
if not h2:
    print("  (仍無 → 看全期)"); [print("  ", t) for t in res2.trades]
