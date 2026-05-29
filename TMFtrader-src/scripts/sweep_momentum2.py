"""
動能評分深度掃描 + 月度明細對比
重點：sess=0.5 為何能多一個 ≥6% 月？
"""
import sys
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy

DATA_PATH = Path('data/historical/tmf_20260411_full_1m.csv')
df_1m = pd.read_csv(DATA_PATH, parse_dates=['datetime']).sort_values('datetime').reset_index(drop=True)
df = df_1m.set_index('datetime')
df_5m = df.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
df_5m = df_5m.between_time('08:45','13:30').reset_index()
ind = precompute_all(df_5m, verbose=False)

BASE = dict(
    sl_atr=2.5, tp_atr=10.0,
    trail_trigger_atr=1.0, trail_dist_atr=1.25, max_bars=80,
    min_adx=20.0, afternoon_min_adx=32.0, min_di_gap=5.0,
    squeeze_ratio=0.90, expand_ratio=1.08, min_vol_ratio=1.0,
    pullback_ema_gap=0.3, breakeven_trigger_atr=999,
    early_cut_bars=25, early_cut_loss_atr=1.5,
    trend_filter=True, ema200_margin_atr=0.0,
)

ALL_MONTHS = ['2024-01','2024-02','2024-03','2024-04','2024-05','2024-06',
              '2024-07','2024-08','2024-09','2024-10','2024-11','2024-12',
              '2025-01','2025-02','2025-03','2025-04','2025-05','2025-06',
              '2025-07','2025-08','2025-09','2025-10','2025-11','2025-12',
              '2026-01','2026-02','2026-03','2026-04']


def run_detail(label, **kwargs):
    p = dict(BASE, **kwargs)
    strat = BreakoutTrendStrategy(**p)
    engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    r = engine.run(df_5m, ind, strat, 'tmf_3x')
    trades = r.trades
    n = len(trades)
    if n == 0:
        print(f"{label}: 0 trades"); return
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses)) if losses else 1
    pf = gp / gl
    wr = len(wins) / n * 100
    net = r.final_balance - 200_000

    monthly = defaultdict(lambda: {'n':0,'wins':0,'pnl':0})
    for t in trades:
        ym = t['entry_time'][:7]
        monthly[ym]['n'] += 1; monthly[ym]['pnl'] += t['pnl']
        if t['pnl'] > 0: monthly[ym]['wins'] += 1

    bal = 200_000.0; n6 = 0; ng = 0; worst = 0
    monthly_ret = {}
    for ym in sorted(monthly):
        m = monthly[ym]
        ret = m['pnl'] / bal * 100
        monthly_ret[ym] = ret
        if ret >= 6: n6 += 1
        if m['pnl'] > 0: ng += 1
        if ret < worst: worst = ret
        bal += m['pnl']

    print(f"\n{'─'*90}")
    print(f"{label}")
    print(f"  {n}筆 WR={wr:.1f}% PF={pf:.3f} 淨利={net:>+,.0f} 報酬={net/200_000*100:+.1f}% G={ng}/28 >=6%={n6} 最差={worst:+.1f}%")
    print()
    # 月度表格
    header = "月份  "
    for ym in ALL_MONTHS:
        header += f"  {ym[5:]}"
    print(header)
    row = "報酬  "
    for ym in ALL_MONTHS:
        if ym in monthly_ret:
            ret = monthly_ret[ym]
            if ret >= 6:
                flag = f"✅{ret:>4.0f}"
            elif ret > 0:
                flag = f"🟢{ret:>4.0f}"
            elif ret < 0:
                flag = f"🔴{ret:>4.0f}"
            else:
                flag = f"  --"
        else:
            flag = f"  --"
        row += f"  {flag}"
    print(row)
    return monthly_ret


print("="*90)
print("月度對比：EMA200-only vs 各動能組合")
print("="*90)

# 1. EMA200-only baseline
m1 = run_detail("EMA200-only（baseline）", use_momentum_score=False)

# 2. sess=0.5 rsi=55/45（掃描中最佳）
m2 = run_detail("EMA200 + sess=0.5 rsi=55/45", use_momentum_score=True,
                momentum_rsi_bull=55.0, momentum_rsi_bear=45.0, momentum_session_atr=0.5)

# 3. 更細 sess 掃描（0.1~0.8）
print("\n" + "="*90)
print("sess 細粒掃描（rsi=55/45）")
print("="*90)
for sess in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]:
    p = dict(BASE, use_momentum_score=True,
             momentum_rsi_bull=55.0, momentum_rsi_bear=45.0,
             momentum_session_atr=sess)
    strat = BreakoutTrendStrategy(**p)
    engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    r = engine.run(df_5m, ind, strat, 'tmf_3x')
    trades = r.trades; n = len(trades)
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses)) if losses else 1
    monthly = defaultdict(float)
    for t in trades: monthly[t['entry_time'][:7]] += t['pnl']
    bal = 200_000.0; n6 = 0; ng = 0; worst = 0
    for ym in sorted(monthly):
        ret = monthly[ym] / bal * 100
        if ret >= 6: n6 += 1
        if monthly[ym] > 0: ng += 1
        if ret < worst: worst = ret
        bal += monthly[ym]
    net = r.final_balance - 200_000
    print(f"  sess={sess:.1f} | {n:>3}筆 WR={len(wins)/n*100:>5.1f}% PF={gp/gl:.3f} "
          f"淨利={net:>+8,.0f} G={ng} >=6%={n6} 最差={worst:+.1f}%")

# 4. sess=0.5 + rsi 細粒掃描
print("\n" + "="*90)
print("sess=0.5 + rsi 細粒掃描")
print("="*90)
for rsi_bull, rsi_bear in [(50,50),(52,48),(53,47),(54,46),(55,45),(56,44),(58,42)]:
    p = dict(BASE, use_momentum_score=True,
             momentum_rsi_bull=rsi_bull, momentum_rsi_bear=rsi_bear,
             momentum_session_atr=0.5)
    strat = BreakoutTrendStrategy(**p)
    engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    r = engine.run(df_5m, ind, strat, 'tmf_3x')
    trades = r.trades; n = len(trades)
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses)) if losses else 1
    monthly = defaultdict(float)
    for t in trades: monthly[t['entry_time'][:7]] += t['pnl']
    bal = 200_000.0; n6 = 0; ng = 0; worst = 0
    for ym in sorted(monthly):
        ret = monthly[ym] / bal * 100
        if ret >= 6: n6 += 1
        if monthly[ym] > 0: ng += 1
        if ret < worst: worst = ret
        bal += monthly[ym]
    net = r.final_balance - 200_000
    print(f"  rsi={rsi_bull}/{rsi_bear} sess=0.5 | {n:>3}筆 WR={len(wins)/n*100:>5.1f}% PF={gp/gl:.3f} "
          f"淨利={net:>+8,.0f} G={ng} >=6%={n6} 最差={worst:+.1f}%")
