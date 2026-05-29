"""
3月份（2026-03）雙策略回測
日盤：BreakoutTrendStrategy（5m K棒，08:45-13:30）
夜盤：ORB B2（vol_ratio<=2.0 + ML>=0.40）
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
import numpy as np
import pickle
from datetime import datetime, time
from pathlib import Path

MONTH_START = '2026-03-01'
MONTH_END   = '2026-03-31'

# ════════════════════════════════════════════════════════
# A. 日盤 — BreakoutTrendStrategy
# ════════════════════════════════════════════════════════
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy

print("載入 1m 資料並重採樣 5m...")
df_1m = pd.read_parquet('data/historical/tmf_5y_1m.parquet')
df_1m['datetime'] = pd.to_datetime(df_1m['datetime'])
df_1m = df_1m.set_index('datetime')
df_5m_all = (
    df_1m.resample('5min')
    .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    .dropna()
    .reset_index()
)

# 只取 3 月日盤
df_day = df_5m_all[
    (df_5m_all['datetime'] >= MONTH_START) &
    (df_5m_all['datetime'] <= MONTH_END + ' 23:59') &
    df_5m_all['datetime'].dt.time.apply(lambda t: time(8, 45) <= t <= time(13, 30))
].copy().reset_index(drop=True)

print(f"日盤 5m: {len(df_day):,} bars  {df_day['datetime'].iloc[0].date()} ~ {df_day['datetime'].iloc[-1].date()}")

PARAMS = dict(
    sl_atr=2.5, tp_atr=10.0,
    trail_trigger_atr=1.75, trail_dist_atr=1.75, max_bars=30,
    min_adx=20.0, min_di_gap=5.0,
    squeeze_ratio=0.78, expand_ratio=1.08,
    min_vol_ratio=1.15, pullback_ema_gap=0.3,
    afternoon_min_adx=32.0, breakeven_trigger_atr=999,
)

print("計算日盤指標...")
ind = precompute_all(df_day, verbose=False)
strat = BreakoutTrendStrategy(**PARAMS)
engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
result = engine.run(df_day, ind, strat, 'tmf_3x')
day_trades = result.trades
day_final = result.final_balance

day_wins   = [t for t in day_trades if t['pnl'] > 0]
day_losses = [t for t in day_trades if t['pnl'] <= 0]
day_gp = sum(t['pnl'] for t in day_wins)
day_gl = abs(sum(t['pnl'] for t in day_losses)) or 1

eq = [200_000.0]; bal = 200_000.0
for t in sorted(day_trades, key=lambda x: x['entry_time']):
    bal += t['pnl']; eq.append(bal)
eq_arr = np.array(eq)
peak   = np.maximum.accumulate(eq_arr)
day_max_dd = ((peak - eq_arr) / peak * 100).max()

# ════════════════════════════════════════════════════════
# B. 夜盤 — ORB B2
# ════════════════════════════════════════════════════════
print("載入 ORB 夜盤信號...")
df_orb = pd.read_parquet('data/historical/ml/TMF_night_orb_signals.parquet')
df_orb['entry_time'] = pd.to_datetime(df_orb['entry_time'])

# 夜盤 entry_time 跨午夜：2026-03-01 21:30 ~ 2026-03-31 04:00
# 取法：entry_date（夜盤開始日）在 3 月
df_orb_mar = df_orb[
    (df_orb['entry_time'] >= '2026-03-01') &
    (df_orb['entry_time'] <  '2026-04-01')
].copy()

with open('optimizer/results_orb/orb_filter_b2.pkl', 'rb') as f:
    model = pickle.load(f)
features = [
    ln.strip()
    for ln in open('optimizer/results_orb/selected_features_b2.txt').readlines()
    if ln.strip()
]

b2_l1 = df_orb_mar[df_orb_mar['f_vol_ratio'] <= 2.0].copy()
if len(b2_l1) > 0:
    probs = model.predict_proba(b2_l1[features])
    b2_l1['ml_prob'] = probs
    night_df = b2_l1[b2_l1['ml_prob'] >= 0.40].copy().sort_values('entry_time').reset_index(drop=True)
else:
    night_df = b2_l1.copy()
    night_df['ml_prob'] = []

RISK_PCT      = 0.04
MAX_CONTRACTS = 3
POINT_VALUE   = 10.0

equity = 200_000.0
pnl_list = []
qty_list = []
for _, row in night_df.iterrows():
    stop_dist = abs(float(row['entry_price']) - float(row['stop_price']))
    if stop_dist < 1e-6:
        stop_dist = float(row['atr_at_entry']) * 2.0
    qty = min(MAX_CONTRACTS, max(1, int(equity * RISK_PCT / (stop_dist * POINT_VALUE))))
    pnl = (float(row['exit_price']) - float(row['entry_price'])) * int(row['direction']) * qty * POINT_VALUE
    pnl_list.append(pnl)
    qty_list.append(qty)
    equity += pnl

night_df['qty']     = qty_list
night_df['pnl_twd'] = pnl_list

night_n     = len(night_df)
night_wins  = int((night_df['pnl_twd'] > 0).sum()) if night_n else 0
night_gp    = float(night_df[night_df['pnl_twd'] > 0]['pnl_twd'].sum()) if night_n else 0
night_gl    = float(abs(night_df[night_df['pnl_twd'] < 0]['pnl_twd'].sum())) if night_n else 1
night_net   = float(night_df['pnl_twd'].sum()) if night_n else 0
night_wr    = night_wins / night_n * 100 if night_n else 0
night_pf    = night_gp / night_gl if night_gl > 1e-6 else 999.0

eq_n   = np.concatenate([[200000.], 200000 + np.cumsum(night_df['pnl_twd'].values)]) if night_n else np.array([200000.])
peak_n = np.maximum.accumulate(eq_n)
night_max_dd = float(((peak_n - eq_n) / peak_n * 100).max()) if night_n else 0.0

# ════════════════════════════════════════════════════════
# C. 輸出結果
# ════════════════════════════════════════════════════════
print()
print("=" * 65)
print("  2026年 3月 雙策略回測結果")
print("=" * 65)

print(f"\n{'─'*65}")
print(f"  日盤 BreakoutTrend v6b  (08:45-13:30，5m K棒)")
print(f"{'─'*65}")
if day_trades:
    print(f"  交易筆數：{len(day_trades)} 筆")
    print(f"  勝率：    {len(day_wins)/len(day_trades)*100:.1f}%  ({len(day_wins)}勝 {len(day_losses)}敗)")
    print(f"  PF：      {day_gp/day_gl:.3f}")
    print(f"  淨利：    {day_final-200_000:+,.0f} TWD")
    print(f"  報酬率：  {(day_final-200_000)/200_000*100:+.2f}%")
    print(f"  MaxDD：   {day_max_dd:.2f}%")
    print()
    print(f"  {'日期':<18} {'方向':<6} {'口數':<4} {'進場':<8} {'出場':<8} {'出場原因':<14} {'損益':>10}")
    print(f"  {'─'*70}")
    for t in day_trades:
        d = t.get('side', t.get('direction', '?')).upper()
        qty_str = f"{t.get('quantity',1)}口"
        reason = t.get('reason', '')[:12]
        print(f"  {t['entry_time'][:16]:<18} {d:<6} {qty_str:<4} {t['entry_price']:>8,.0f} {t['exit_price']:>8,.0f}  {reason:<14} {t['pnl']:>+10,.0f}")
else:
    print("  本月無交易（市場條件不符）")

print(f"\n{'─'*65}")
print(f"  夜盤 ORB B2+ML  (21:30-04:00，vol_ratio≤2.0，ML≥0.40)")
print(f"{'─'*65}")
print(f"  3月 raw 信號：{len(df_orb_mar)} 筆")
print(f"  vol_ratio≤2.0：{len(b2_l1)} 筆")
if night_n:
    print(f"  ML 通過 (≥0.40)：{night_n} 筆")
    print()
    print(f"  交易筆數：{night_n} 筆")
    print(f"  勝率：    {night_wr:.1f}%  ({night_wins}勝 {night_n-night_wins}敗)")
    print(f"  PF：      {night_pf:.3f}")
    print(f"  淨利：    {night_net:+,.0f} TWD")
    print(f"  報酬率：  {night_net/200_000*100:+.2f}%")
    print(f"  MaxDD：   {night_max_dd:.2f}%")
    print()
    dir_map = {1: 'LONG', -1: 'SHORT'}
    print(f"  {'日期':<18} {'方向':<6} {'口數':<4} {'進場':<8} {'出場':<8} {'ML機率':<8} {'損益':>10}")
    print(f"  {'─'*65}")
    for _, row in night_df.iterrows():
        d = dir_map.get(int(row['direction']), '?')
        print(f"  {str(row['entry_time'])[:16]:<18} {d:<6} {int(row['qty'])}口   "
              f"{row['entry_price']:>8,.0f} {row['exit_price']:>8,.0f} "
              f"  {row['ml_prob']:.3f}   {row['pnl_twd']:>+10,.0f}")
else:
    print(f"  ML 通過 (≥0.40)：0 筆（本月無夜盤交易）")

combined_net = (day_final - 200_000) + night_net
print(f"\n{'─'*65}")
print(f"  合計淨利（日+夜）：{combined_net:+,.0f} TWD")
print(f"  合計報酬率：       {combined_net/200_000*100:+.2f}%（各以 20萬計算）")
print("=" * 65)
