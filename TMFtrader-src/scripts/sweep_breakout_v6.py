"""
BreakoutTrend 參數掃描 v6
從現有 64-trade, PF=2.203 基線出發，系統性搜尋更好的參數組合。

執行：
    cd TMFtrader-src
    python scripts/sweep_breakout_v6.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
import numpy as np
from itertools import product
from collections import defaultdict
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy

# ── 載入資料 ─────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
CSV = ROOT / 'data' / 'historical' / 'tmf_20260411_full_1m.csv'

print('Loading data...')
df_1m = pd.read_csv(CSV, parse_dates=['datetime']).sort_values('datetime').reset_index(drop=True)
df = df_1m.set_index('datetime')
df_5m = df.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
df_5m = df_5m.between_time('08:45', '13:30').reset_index()
print(f'  5min bars: {len(df_5m)}  {df_5m["datetime"].iloc[0]} ~ {df_5m["datetime"].iloc[-1]}')

print('Precomputing indicators...')
ind = precompute_all(df_5m, verbose=False)

# ── 固定參數（v5 已確認最佳） ─────────────────────────────────────────────
BASE = dict(
    sl_atr=2.5, tp_atr=10.0,
    squeeze_ratio=0.90,
    min_vol_ratio=1.0,
    breakeven_trigger_atr=999,
    max_loss_twd=4000.0,
    trend_filter=True, ema200_margin_atr=0.0,
    use_momentum_score=True,
    momentum_rsi_bull=52.0, momentum_rsi_bear=46.0, momentum_session_atr=0.5,
    point_value=10.0,
    scale_out_trigger_atr=0.0, scale_out_qty=1,
)

# ── 掃描範圍 ─────────────────────────────────────────────────────────────
GRID = {
    'expand_ratio':        [1.12, 1.15, 1.18, 1.20],
    'min_di_gap':          [8.0, 10.0, 12.0],
    'afternoon_min_adx':   [22.0, 26.0, 30.0, 32.0],
    'min_adx':             [18.0, 20.0, 22.0],
    'squeeze_grace_bars':  [0, 1, 2],
    'trail_trigger_atr':   [1.0, 1.2, 1.5],
    'trail_dist_atr':      [1.0, 1.25, 1.5],
    'pullback_ema_gap':    [0.15, 0.20, 0.25],
    'early_cut_bars':      [25, 30, 40],
    'max_bars':            [60, 80, 100],
}

# 分階段掃描（避免組合爆炸）

# ── Stage A：主要進場參數（固定出場為 v5 最佳）─────────────────────────
print('\n' + '='*70)
print('Stage A: 進場參數掃描 (expand / di_gap / afternoon_adx / min_adx / grace)')
print('='*70)

FIXED_EXIT = dict(
    trail_trigger_atr=1.2, trail_dist_atr=1.25,
    max_bars=80, early_cut_bars=30, early_cut_loss_atr=1.5,
)

results_a = []
combos_a = list(product(
    GRID['expand_ratio'],
    GRID['min_di_gap'],
    GRID['afternoon_min_adx'],
    GRID['min_adx'],
    GRID['squeeze_grace_bars'],
))
print(f'Total combinations: {len(combos_a)}')

for i, (er, dig, amad, mad, grace) in enumerate(combos_a):
    if mad > amad:   # min_adx can't exceed afternoon_min_adx
        continue
    params = {**BASE, **FIXED_EXIT,
        'expand_ratio': er, 'min_di_gap': dig, 'afternoon_min_adx': amad,
        'min_adx': mad, 'squeeze_grace_bars': grace,
        'pullback_ema_gap': 0.20,
    }
    strat = BreakoutTrendStrategy(**params)
    engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    result = engine.run(df_5m, ind, strat, 'tmf_3x')
    trades = result.trades; n = len(trades)
    if n < 20:
        continue
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gp = sum(t['pnl'] for t in wins); gl = abs(sum(t['pnl'] for t in losses)) if losses else 1
    pf = gp / gl; wr = len(wins) / n * 100
    net = result.final_balance - 200_000
    monthly = defaultdict(float)
    for t in trades: monthly[t['entry_time'][:7]] += t['pnl']
    ng = sum(1 for v in monthly.values() if v > 0)
    b = 200_000.0; n6 = 0
    for ym in sorted(monthly):
        if monthly[ym] / b >= 0.06: n6 += 1
        b += monthly[ym]
    eq = result.equity_curve; peak = eq[0]; max_dd = 0.0
    for v in eq:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd: max_dd = dd
    results_a.append({
        'er': er, 'dig': dig, 'amad': amad, 'mad': mad, 'grace': grace,
        'n': n, 'wr': wr, 'pf': pf, 'net': net, 'max_dd': max_dd,
        'ng': ng, 'n6': n6, 'nm': len(monthly),
    })

# 排序：PF 優先，再看 MaxDD
results_a.sort(key=lambda x: (-x['pf'], x['max_dd']))

print(f'\nTop 20 by PF (n >= 20):')
print(f"{'er':>5} {'dig':>5} {'amad':>5} {'mad':>5} {'grc':>3} | {'n':>4} {'WR':>6} {'PF':>6} {'MaxDD':>7} {'net':>9} {'g/m':>5} {'n6':>3}")
print('-' * 80)
for r in results_a[:20]:
    print(f"{r['er']:>5.2f} {r['dig']:>5.1f} {r['amad']:>5.1f} {r['mad']:>5.1f} {r['grace']:>3d} | "
          f"{r['n']:>4d} {r['wr']:>5.1f}% {r['pf']:>6.3f} {r['max_dd']:>6.1f}% {r['net']:>+9,.0f} "
          f"{r['ng']}/{r['nm']} {r['n6']:>3d}")

# Find best balanced score
def score(r):
    if r['pf'] < 1.5 or r['max_dd'] > 15 or r['n'] < 40:
        return -999
    return r['pf'] * 0.4 + (r['wr'] / 100) * 0.2 + (r['n6'] / 28) * 0.2 - (r['max_dd'] / 100) * 0.2

results_a.sort(key=lambda x: -score(x))
top = [r for r in results_a if score(r) > -999][:5]
print(f'\n=== TOP 5 Balanced (PF≥1.5, MaxDD≤15%, n≥40) ===')
for r in top:
    print(f"  er={r['er']} dig={r['dig']} amad={r['amad']} mad={r['mad']} grace={r['grace']}: "
          f"n={r['n']} WR={r['wr']:.1f}% PF={r['pf']:.3f} MaxDD={r['max_dd']:.1f}% "
          f"net={r['net']:+,.0f} n6={r['n6']}")

if not top:
    print('  (no combo meets criteria)')
    best_a = results_a[0] if results_a else None
else:
    best_a = top[0]

# ── Stage B：出場參數掃描（固定最佳進場） ──────────────────────────────────
if best_a:
    print('\n' + '='*70)
    print(f'Stage B: 出場參數掃描 (固定進場: er={best_a["er"]} dig={best_a["dig"]} amad={best_a["amad"]} mad={best_a["mad"]} grace={best_a["grace"]})')
    print('='*70)

    results_b = []
    combos_b = list(product(
        GRID['trail_trigger_atr'],
        GRID['trail_dist_atr'],
        GRID['max_bars'],
        GRID['early_cut_bars'],
    ))
    print(f'Total combinations: {len(combos_b)}')

    for trig, dist, mb, ecb in combos_b:
        params = dict(BASE,
            expand_ratio=best_a['er'], min_di_gap=best_a['dig'],
            afternoon_min_adx=best_a['amad'], min_adx=best_a['mad'],
            squeeze_grace_bars=best_a['grace'], pullback_ema_gap=0.20,
            trail_trigger_atr=trig, trail_dist_atr=dist,
            max_bars=mb, early_cut_bars=ecb, early_cut_loss_atr=1.5,
        )
        strat = BreakoutTrendStrategy(**params)
        engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
        result = engine.run(df_5m, ind, strat, 'tmf_3x')
        trades = result.trades; n = len(trades)
        if n < 20: continue
        wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]
        gp = sum(t['pnl'] for t in wins); gl = abs(sum(t['pnl'] for t in losses)) if losses else 1
        pf = gp / gl; wr = len(wins) / n * 100
        net = result.final_balance - 200_000
        monthly = defaultdict(float)
        for t in trades: monthly[t['entry_time'][:7]] += t['pnl']
        ng = sum(1 for v in monthly.values() if v > 0)
        b = 200_000.0; n6 = 0
        for ym in sorted(monthly):
            if monthly[ym] / b >= 0.06: n6 += 1
            b += monthly[ym]
        eq = result.equity_curve; peak = eq[0]; max_dd = 0.0
        for v in eq:
            if v > peak: peak = v
            dd = (peak - v) / peak * 100
            if dd > max_dd: max_dd = dd
        results_b.append({
            'trig': trig, 'dist': dist, 'mb': mb, 'ecb': ecb,
            'n': n, 'wr': wr, 'pf': pf, 'net': net, 'max_dd': max_dd,
            'ng': ng, 'n6': n6, 'nm': len(monthly),
        })

    results_b.sort(key=lambda x: -score(x) if score(x) > -999 else x['pf'])
    print(f'\nTop 15 exit combinations:')
    print(f"{'trig':>5} {'dist':>5} {'mb':>4} {'ecb':>4} | {'n':>4} {'WR':>6} {'PF':>6} {'MaxDD':>7} {'net':>9} {'n6':>3}")
    print('-' * 65)
    for r in results_b[:15]:
        print(f"{r['trig']:>5.2f} {r['dist']:>5.2f} {r['mb']:>4d} {r['ecb']:>4d} | "
              f"{r['n']:>4d} {r['wr']:>5.1f}% {r['pf']:>6.3f} {r['max_dd']:>6.1f}% {r['net']:>+9,.0f} {r['n6']:>3d}")

print('\n' + '='*70)
print('掃描完成')
