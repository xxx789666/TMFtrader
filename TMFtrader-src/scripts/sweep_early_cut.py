"""
早切止損參數掃描
目標：控制單筆虧損 ≤ 4,000 TWD（初始資金 200,000 的 2%）
掃描 early_cut_bars × early_cut_loss_atr 所有組合
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
    trend_filter=True, ema200_margin_atr=0.0,
    use_momentum_score=True,
    momentum_rsi_bull=52.0, momentum_rsi_bear=48.0, momentum_session_atr=0.5,
)
MAX_LOSS_LIMIT = 4_000   # 2% of 200,000

def exit_label(r):
    if '追蹤' in r: return 'TRAIL'
    if '早切' in r: return 'EARLY'
    if '時間' in r: return 'TIME'
    return 'EOD'


def run(ec_bars, ec_loss):
    p = dict(BASE, early_cut_bars=ec_bars, early_cut_loss_atr=ec_loss)
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

    # 早切統計
    early = [t for t in trades if exit_label(t['reason']) == 'EARLY']
    early_losses = [t for t in early if t['pnl'] <= 0]
    worst_early = min(t['pnl'] for t in early) if early else 0
    over_limit = sum(1 for t in early if t['pnl'] < -MAX_LOSS_LIMIT)
    n_early = len(early)

    # 月度
    monthly = defaultdict(float)
    monthly_bal = defaultdict(lambda: {'n':0,'wins':0,'pnl':0})
    for t in trades:
        ym = t['entry_time'][:7]
        monthly[ym] += t['pnl']
        monthly_bal[ym]['n'] += 1
        monthly_bal[ym]['pnl'] += t['pnl']
        if t['pnl'] > 0: monthly_bal[ym]['wins'] += 1

    bal = 200_000.0; n6 = 0; ng = 0; worst_m = 0
    for ym in sorted(monthly):
        ret = monthly[ym] / bal * 100
        if ret >= 6: n6 += 1
        if monthly[ym] > 0: ng += 1
        if ret < worst_m: worst_m = ret
        bal += monthly[ym]

    return dict(
        ec_bars=ec_bars, ec_loss=ec_loss,
        n=n, wr=wr, pf=pf, net=net,
        n6=n6, ng=ng, worst_m=worst_m,
        n_early=n_early, worst_early=worst_early, over_limit=over_limit,
    )


# ── 全量掃描
bars_list = [10, 15, 20, 25, 30]
loss_list = [0.8, 1.0, 1.2, 1.3, 1.5, 1.8, 2.0]

print("="*110)
print(f"{'bars':>5} {'loss':>5} | {'筆':>4} {'WR':>6} {'PF':>6} {'淨利':>9} {'報酬':>7} | {'G':>3} {'≥6%':>4} {'最差月':>7} | {'早切N':>5} {'超限N':>5} {'早切最差':>10}")
print("-"*110)

results = []
for ec_bars in bars_list:
    for ec_loss in loss_list:
        r = run(ec_bars, ec_loss)
        if r is None: continue
        results.append(r)
        flag = '' if r['over_limit'] == 0 else ' *' + str(r['over_limit'])
        net_m = r['net']/200_000*100
        print(f"  {ec_bars:>3} {ec_loss:>5.1f} | {r['n']:>4} {r['wr']:>5.1f}% {r['pf']:>6.3f} {r['net']:>+9,.0f} {net_m:>+6.1f}% | "
              f"{r['ng']:>3} {r['n6']:>4} {r['worst_m']:>+7.1f}% | "
              f"{r['n_early']:>5} {r['over_limit']:>5}{flag} {r['worst_early']:>+10,.0f}")
    print()

# ── 篩選：早切無超限（單筆 ≤ 4,000）的所有組合
print("="*110)
print(f"【篩選】單筆虧損全部 ≤ {MAX_LOSS_LIMIT:,} TWD（2% 上限）的組合")
print("="*110)
ok = [r for r in results if r['over_limit'] == 0]
if not ok:
    print("  無完全符合的組合")
else:
    ok_sorted = sorted(ok, key=lambda x: (x['n6'], x['net']), reverse=True)
    print(f"{'bars':>5} {'loss':>5} | {'筆':>4} {'WR':>6} {'PF':>6} {'淨利':>9} {'報酬':>7} | {'≥6%':>4} {'最差月':>7} | {'早切最差':>10}")
    for r in ok_sorted:
        net_m = r['net']/200_000*100
        print(f"  {r['ec_bars']:>3} {r['ec_loss']:>5.1f} | {r['n']:>4} {r['wr']:>5.1f}% {r['pf']:>6.3f} {r['net']:>+9,.0f} {net_m:>+6.1f}% | "
              f"{r['n6']:>4} {r['worst_m']:>+7.1f}% | {r['worst_early']:>+10,.0f}")

# ── 當前參數對比
print()
print("="*110)
print("【當前參數 ec_bars=25 loss=1.5 的基準】")
baseline = next((r for r in results if r['ec_bars']==25 and abs(r['ec_loss']-1.5)<0.01), None)
if baseline:
    print(f"  bars=25 loss=1.5 | n={baseline['n']} WR={baseline['wr']:.1f}% PF={baseline['pf']:.3f} "
          f"淨利={baseline['net']:+,.0f} ≥6%={baseline['n6']} 最差月={baseline['worst_m']:+.1f}% "
          f"早切最差={baseline['worst_early']:+,.0f} 超限={baseline['over_limit']}筆")
