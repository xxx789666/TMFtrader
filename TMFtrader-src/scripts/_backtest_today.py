"""
即興回測：跑 BreakoutTrendStrategy on 5/22 早盤實際 K 棒、看會不會 fire entry。

用法：
  python3 scripts/_backtest_today.py [DATE]
  default: 今天日期、不含日期當天分數

輸出：每根 5-min bar 的 strategy 判斷結果、entry signal 列表。
"""
import os, sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import numpy as np
import pandas as pd

import shioaji as sj
api = sj.Shioaji(simulation=False)
api.login(api_key=os.environ["SHIOAJI_API_KEY"], secret_key=os.environ["SHIOAJI_SECRET_KEY"])
c = api.Contracts.Futures.TMF.TMFR1

# 抓回測範圍：今早 8:00 - 14:00 + 之前 8 天 warmup 給 EMA200 足夠資料
target_date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
end_dt = datetime.strptime(target_date, "%Y-%m-%d").replace(hour=14, minute=0)
start_dt = end_dt - timedelta(days=10)  # 10 天 warmup
print(f"Loading kbars {start_dt} to {end_dt}...")

kb = api.kbars(c, start=start_dt.strftime("%Y-%m-%d"), end=end_dt.strftime("%Y-%m-%d"))
df = pd.DataFrame({
    "datetime": pd.to_datetime(kb.ts),
    "open": kb.Open, "high": kb.High, "low": kb.Low,
    "close": kb.Close, "volume": kb.Volume,
})
df = df.sort_values("datetime").reset_index(drop=True)
print(f"Raw bars: {len(df)}, range: {df.datetime.min()} ~ {df.datetime.max()}")

# Resample 到 1-min（如果不是）然後再聚到 5-min
df = df.set_index("datetime")
df5 = df.resample("5min").agg({
    "open": "first", "high": "max", "low": "min",
    "close": "last", "volume": "sum",
}).dropna().reset_index()
print(f"5-min bars: {len(df5)}")

# 限縮回測範圍：8:00-14:00 today + 200 bars warmup
day_start = end_dt.replace(hour=8, minute=0)
warmup_df = df5[df5.datetime < day_start].tail(400)  # 400 bars warmup（給 ema200）
target_df = df5[(df5.datetime >= day_start) & (df5.datetime < end_dt)]
print(f"Warmup bars: {len(warmup_df)}, target bars (8:00-14:00 today): {len(target_df)}")

# === 跑 BreakoutTrendStrategy ===
from core.market_data import KBar, MarketSnapshot, IndicatorEngine
from strategy.breakout import BreakoutTrendStrategy

strat = BreakoutTrendStrategy()
ind = IndicatorEngine()

# 餵 warmup K 棒、不評估訊號
full_df = pd.concat([warmup_df, target_df]).reset_index(drop=True)
signals = []
print(f"\n=== 逐根回測 (共 {len(full_df)} bars、warmup {len(warmup_df)})===\n")

for i, row in full_df.iterrows():
    # 構造 KBar + 用前面所有 bars 算 snapshot
    bars_so_far = full_df.iloc[: i + 1].copy()
    if len(bars_so_far) < 50:
        continue   # 太少 bar、跳過

    # IndicatorEngine 需要 DataFrame
    snap = ind.update(bars_so_far)
    if snap is None or snap.price is None:
        continue

    kb_obj = KBar(
        datetime=row.datetime,
        open=float(row.open), high=float(row.high),
        low=float(row.low), close=float(row.close),
        volume=int(row.volume),
        interval=5,
    )

    # 只在 target window 內評估
    if row.datetime < day_start:
        continue

    sig = strat.on_kbar(kb_obj, snap)

    if sig:
        signals.append({
            "time": row.datetime,
            "side": sig.direction.value if hasattr(sig.direction, "value") else str(sig.direction),
            "price": float(row.close),
            "stop_loss": getattr(sig, "stop_loss", None),
            "take_profit": getattr(sig, "take_profit", None),
            "reason": getattr(sig, "reason", "") or getattr(sig, "rationale", ""),
            "strength": getattr(sig, "strength", 0.0),
        })
        print(f"🎯 {row.datetime} {sig.direction} @ {row.close:.0f} | SL={getattr(sig,'stop_loss',0):.0f} | {getattr(sig,'reason','')}")

print(f"\n=== 結果 ===")
print(f"target window 內 entry signals: {len(signals)}")
for i, s in enumerate(signals):
    print(f"  #{i+1} {s['time']} {s['side']} @ {s['price']:.0f} | SL={s.get('stop_loss',0):.0f} | {s.get('reason','')}")

api.logout()
