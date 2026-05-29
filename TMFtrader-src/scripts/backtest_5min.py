"""
5 分鐘 K 線回測腳本
將 1 分鐘資料重採樣至 5 分鐘，使用相同策略邏輯
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
import numpy as np
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy
from collections import defaultdict


def resample_to_5min(df_1m: pd.DataFrame) -> pd.DataFrame:
    """將 1 分鐘 OHLCV 重採樣至 5 分鐘"""
    df = df_1m.copy()
    df = df.set_index('datetime')
    df_5m = df.resample('5min').agg({
        'open':   'first',
        'high':   'max',
        'low':    'min',
        'close':  'last',
        'volume': 'sum',
    }).dropna()
    # 過濾只保留交易時段 08:45-13:30
    df_5m = df_5m.between_time('08:45', '13:30')
    df_5m = df_5m.reset_index()
    print(f"  1min: {len(df_1m)} bars → 5min: {len(df_5m)} bars")
    print(f"  5min range: {df_5m['datetime'].iloc[0]} ~ {df_5m['datetime'].iloc[-1]}")
    return df_5m


def run_backtest(df, label, **params):
    indicators = precompute_all(df, verbose=False)
    strat = BreakoutTrendStrategy(**params)
    engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    result = engine.run(df, indicators, strat, 'tmf_3x')

    trades = result.trades
    n = len(trades)
    if n == 0:
        print(f"{label}: 0 trades")
        return None

    wins   = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses)) if losses else 1
    pf = gp / gl
    wr = len(wins) / n * 100
    avg_w = gp / len(wins) if wins else 0
    avg_l = gl / len(losses) if losses else 0

    monthly = defaultdict(float)
    for t in trades:
        monthly[t['entry_time'][:7]] += t['pnl']
    n_green = sum(1 for v in monthly.values() if v > 0)
    b2 = 200_000.0; n6 = 0
    for ym in sorted(monthly):
        if monthly[ym] / b2 >= 0.06: n6 += 1
        b2 += monthly[ym]
    net = result.final_balance - 200_000

    reasons = defaultdict(lambda: {'n': 0, 'pnl': 0, 'wins': 0})
    for t in trades:
        r = t['reason']
        k = 'TRAIL' if '追蹤' in r else ('TIME' if '時間' in r else 'EOD')
        reasons[k]['n'] += 1; reasons[k]['pnl'] += t['pnl']
        if t['pnl'] > 0: reasons[k]['wins'] += 1

    tr = reasons['TRAIL']; ti = reasons['TIME']
    bars_per_day = n / (len(monthly) * 20) * 5  # rough estimate
    print(f"\n{label}")
    print(f"  {n}筆 WR={wr:.1f}% PF={pf:.3f} avgW={avg_w:+,.0f} avgL=-{avg_l:,.0f} ratio={avg_w/avg_l:.2f}")
    print(f"  淨利={net:+,.0f} TWD  總報酬={net/200_000*100:+.1f}%  年化≈{net/200_000*100/2.28:.0f}%")
    print(f"  綠月={n_green}/28  達標={n6}個 (>=6%)")
    print(f"  TRAIL={tr['n']}筆(WR={tr['wins']/tr['n']*100:.0f}% avg={tr['pnl']/tr['n']:+,.0f}) "
          f"TIME={ti['n']}筆(WR={ti['wins']/ti['n']*100 if ti['n'] else 0:.0f}% avg={ti['pnl']/ti['n']:+,.0f})" if ti['n'] else
          f"  TRAIL={tr['n']}筆(WR={tr['wins']/tr['n']*100:.0f}% avg={tr['pnl']/tr['n']:+,.0f}) TIME=0筆")

    # 月度明細
    monthly_d = defaultdict(lambda: {'n': 0, 'wins': 0, 'pnl': 0})
    for t in trades:
        ym = t['entry_time'][:7]
        monthly_d[ym]['n'] += 1; monthly_d[ym]['pnl'] += t['pnl']
        if t['pnl'] > 0: monthly_d[ym]['wins'] += 1
    bal = 200_000.0
    print("  月度:")
    for ym in sorted(monthly_d.keys()):
        m = monthly_d[ym]
        ret = m['pnl'] / bal * 100
        flag = '✅' if ret >= 6 else ('🟢' if m['pnl'] > 0 else '🔴')
        print(f"    {ym}: {m['n']:>2}筆 {m['wins']}/{m['n']-m['wins']:<2} {m['pnl']:>+7,.0f} {ret:>+5.1f}% {flag}")
        bal += m['pnl']
    return result


# ── 載入資料 ────────────────────────────────────────────
DATA_PATH = Path('data/historical/tmf_20260411_full_1m.csv')
print("Loading 1-min data...")
df_1m = pd.read_csv(DATA_PATH, parse_dates=['datetime']).sort_values('datetime').reset_index(drop=True)
print(f"  1min bars: {len(df_1m)}")

print("\nResampling to 5-min...")
df_5m = resample_to_5min(df_1m)

# ── 1 分鐘基準（目前最佳）──────────────────────────────
PARAMS_1M = dict(
    sl_atr=2.5, tp_atr=10.0,
    trail_trigger_atr=1.75, trail_dist_atr=1.75, max_bars=150,
    min_adx=20.0, min_di_gap=5.0,
    squeeze_ratio=0.78, expand_ratio=1.08,
    min_vol_ratio=1.15, pullback_ema_gap=0.3,
    afternoon_min_adx=32.0, breakeven_trigger_atr=999,
)

# ── 5 分鐘參數（max_bars 換算：150min/5=30根 = 2.5h）──
PARAMS_5M_BASE = dict(
    sl_atr=2.5, tp_atr=10.0,
    trail_trigger_atr=1.75, trail_dist_atr=1.75, max_bars=30,
    min_adx=20.0, min_di_gap=5.0,
    squeeze_ratio=0.78, expand_ratio=1.08,
    min_vol_ratio=1.15, pullback_ema_gap=0.3,
    afternoon_min_adx=32.0, breakeven_trigger_atr=999,
)

print("\n" + "="*70)
print("基準：1 分鐘 K 線（目前最佳）")
print("="*70)
ind_1m = precompute_all(df_1m, verbose=False)
strat_1m = BreakoutTrendStrategy(**PARAMS_1M)
engine_1m = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
r1m = engine_1m.run(df_1m, ind_1m, strat_1m, 'tmf_3x')
t1 = r1m.trades; n1 = len(t1)
wins1 = [t for t in t1 if t['pnl']>0]; losses1 = [t for t in t1 if t['pnl']<=0]
gp1=sum(t['pnl'] for t in wins1); gl1=abs(sum(t['pnl'] for t in losses1))
m1=defaultdict(float)
for t in t1: m1[t['entry_time'][:7]] += t['pnl']
ng1=sum(1 for v in m1.values() if v>0); b=200_000.0; n6_1=0
for ym in sorted(m1):
    if m1[ym]/b>=0.06: n6_1+=1
    b+=m1[ym]
print(f"  {n1}筆 WR={len(wins1)/n1*100:.1f}% PF={gp1/gl1:.3f} avgW={gp1/len(wins1):+,.0f} 淨利={r1m.final_balance-200_000:+,.0f} 報酬={( r1m.final_balance-200_000)/200_000*100:+.1f}% G={ng1}/28 >=6%={n6_1}")

print("\n" + "="*70)
print("5 分鐘 K 線 — max_bars sweep")
print("="*70)
ind_5m = precompute_all(df_5m, verbose=False)

for mb in [15, 20, 25, 30, 36, 42, 50, 60]:
    p = dict(PARAMS_5M_BASE, max_bars=mb)
    strat = BreakoutTrendStrategy(**p)
    engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    result = engine.run(df_5m, ind_5m, strat, 'tmf_3x')
    trades = result.trades; n = len(trades)
    if n == 0:
        print(f"  max_bars={mb}: 0 trades"); continue
    wins = [t for t in trades if t['pnl']>0]; losses = [t for t in trades if t['pnl']<=0]
    gp=sum(t['pnl'] for t in wins); gl=abs(sum(t['pnl'] for t in losses)) if losses else 1
    monthly=defaultdict(float)
    for t in trades: monthly[t['entry_time'][:7]] += t['pnl']
    ng=sum(1 for v in monthly.values() if v>0); b=200_000.0; n6=0
    for ym in sorted(monthly):
        if monthly[ym]/b>=0.06: n6+=1
        b+=monthly[ym]
    net=result.final_balance-200_000
    reasons=defaultdict(lambda:{'n':0,'pnl':0,'wins':0})
    for t in trades:
        r=t['reason']
        k='TRAIL' if '追蹤' in r else ('TIME' if '時間' in r else 'EOD')
        reasons[k]['n']+=1; reasons[k]['pnl']+=t['pnl']
        if t['pnl']>0: reasons[k]['wins']+=1
    tr=reasons['TRAIL']; ti=reasons['TIME']
    print(f"  5min max_bars={mb:>3} ({mb*5}min) | {n:>3}筆 WR={len(wins)/n*100:>5.1f}% PF={gp/gl:>6.3f} "
          f"avgW={gp/len(wins) if wins else 0:>+6,.0f} 淨利={net:>+8,.0f} {net/200_000*100:>+5.1f}% "
          f"G={ng}/28 >=6%={n6} | TRAIL={tr['n']} TIME={ti['n']}")
