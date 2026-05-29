"""
金額硬止損掃描
目標：單筆虧損 ≤ 4,000 TWD，找到對整體績效影響最小的設定
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
    use_momentum_score=True,
    momentum_rsi_bull=52.0, momentum_rsi_bear=48.0, momentum_session_atr=0.5,
    point_value=10.0,
)
LIMIT = 4_000


def exit_cat(reason):
    if '追蹤' in reason: return 'TRAIL'
    if '早切' in reason: return 'EARLY'
    if '時間' in reason: return 'TIME'
    if '金額' in reason: return 'HARDSTOP'
    return 'OTHER'


def run(max_loss_twd=0.0):
    p = dict(BASE, max_loss_twd=max_loss_twd)
    strat = BreakoutTrendStrategy(**p)
    engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    r = engine.run(df_5m, ind, strat, 'tmf_3x')
    trades = r.trades
    n = len(trades)
    if n == 0:
        return None

    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses)) if losses else 1
    pf = gp / gl
    wr = len(wins) / n * 100
    net = r.final_balance - 200_000

    cats = defaultdict(lambda: {'n':0,'pnl':0})
    for t in trades:
        k = exit_cat(t['reason'])
        cats[k]['n'] += 1; cats[k]['pnl'] += t['pnl']

    worst = min(t['pnl'] for t in losses) if losses else 0
    over = sum(1 for t in losses if t['pnl'] < -LIMIT)

    monthly = defaultdict(float)
    for t in trades:
        monthly[t['entry_time'][:7]] += t['pnl']
    bal = 200_000.0; n6 = 0; ng = 0; worst_m = 0
    for ym in sorted(monthly):
        ret = monthly[ym] / bal * 100
        if ret >= 6: n6 += 1
        if monthly[ym] > 0: ng += 1
        if ret < worst_m: worst_m = ret
        bal += monthly[ym]

    return dict(
        max_loss=max_loss_twd, n=n, wr=wr, pf=pf, net=net,
        n6=n6, ng=ng, worst_m=worst_m,
        worst_trade=worst, over_limit=over,
        cats=dict(cats),
    )


print("=" * 105)
print(f"  金額止損  |  筆數   WR     PF        淨利   報酬 |  G  >=6%  最差月 | 最差單筆  超限 | TRAIL  EARLY  HARD")
print("-" * 105)

results = []
for ml in [0, 2000, 2500, 3000, 3500, 4000, 4500, 5000]:
    r = run(ml)
    if r is None: continue
    results.append(r)
    cats = r['cats']
    trail_n = cats.get('TRAIL', {}).get('n', 0)
    early_n = cats.get('EARLY', {}).get('n', 0)
    hard_n  = cats.get('HARDSTOP', {}).get('n', 0)
    label = f"{ml:>6,.0f}" if ml > 0 else "  停用"
    flag = '' if r['over_limit'] == 0 else f' *{r["over_limit"]}'
    print(f"  {label} TWD | {r['n']:>4}  {r['wr']:>5.1f}%  {r['pf']:>6.3f}  {r['net']:>+9,.0f}  {r['net']/200_000*100:>+5.1f}% | "
          f"{r['ng']:>3} {r['n6']:>3}   {r['worst_m']:>+6.1f}% | {r['worst_trade']:>+8,.0f}{flag:>4} | "
          f"{trail_n:>5}  {early_n:>5}  {hard_n:>5}")

print()
print("=" * 105)
print("【符合 2% 上限（≤4,000 TWD）的組合月度明細】")
ok = [r for r in results if r['over_limit'] == 0 and r['max_loss'] > 0]
if not ok:
    print("  無完全符合的組合")
else:
    best = ok[0]
    print(f"  max_loss_twd={best['max_loss']:,.0f} | n6={best['n6']} PF={best['pf']:.3f} 淨利={best['net']:+,.0f}")

# 月度明細對比：原始 vs 最佳
print()
print("=" * 105)
print("月度明細對比（停用 vs 金額止損 4,000）")
print("=" * 105)

for ml, label in [(0, '停用'), (4000, '4,000')]:
    p = dict(BASE, max_loss_twd=float(ml))
    strat = BreakoutTrendStrategy(**p)
    engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    r2 = engine.run(df_5m, ind, strat, 'tmf_3x')
    trades2 = r2.trades
    monthly2 = defaultdict(lambda: {'n':0,'wins':0,'pnl':0})
    for t in trades2:
        ym = t['entry_time'][:7]
        monthly2[ym]['n'] += 1; monthly2[ym]['pnl'] += t['pnl']
        if t['pnl'] > 0: monthly2[ym]['wins'] += 1
    print(f"\n  [{label}]")
    bal = 200_000.0; n6 = 0; worst = 0
    for ym in sorted(monthly2):
        m = monthly2[ym]
        ret = m['pnl'] / bal * 100
        fl = 'OK' if ret >= 6 else ('+ ' if m['pnl'] > 0 else '- ')
        if ret >= 6: n6 += 1
        if ret < worst: worst = ret
        print(f"    {ym}: {m['n']:>2}筆 {m['wins']}/{m['n']-m['wins']:<2} {m['pnl']:>+7,.0f} {ret:>+5.1f}% {fl}")
        bal += m['pnl']
    net2 = r2.final_balance - 200_000
    print(f"    => 淨利={net2:+,.0f} >=6%={n6}/28 最差={worst:+.1f}%")
