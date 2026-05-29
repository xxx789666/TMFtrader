"""
頻率優先參數掃描 — 目標 15 筆/月
========================================
與 sweep_breakout_v6.py 的差異：
  1. 使用全時段資料（日盤 + 夜盤），不再只篩 08:45-13:30
  2. 目標函數改為「頻率優先」：先篩 n/month ≥ 10，再最大化 WR & PF
  3. 大幅放寬參數範圍（min_adx 8-20、expand_ratio 1.04-1.14 等）

執行：
    cd TMFtrader-src
    python scripts/sweep_freq_first.py
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

# ── 載入資料（全時段，不做 between_time 篩選）────────────────────────────
ROOT = Path(__file__).parent.parent
CSV = ROOT / 'data' / 'historical' / 'tmf_20260411_full_1m.csv'

print('Loading data (all sessions)...')
df_1m = pd.read_csv(CSV, parse_dates=['datetime']).sort_values('datetime').reset_index(drop=True)
df = df_1m.set_index('datetime')
df_5m = df.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
# 不過濾時段 — 讓策略的 in_day / in_night 邏輯自行處理
df_5m = df_5m.reset_index()

n_months = (df_5m['datetime'].iloc[-1] - df_5m['datetime'].iloc[0]).days / 30.44
print(f'  5min bars: {len(df_5m):,}  {df_5m["datetime"].iloc[0]} ~ {df_5m["datetime"].iloc[-1]}')
print(f'  Period: {n_months:.1f} months')

print('Precomputing indicators...')
ind = precompute_all(df_5m, verbose=False)

# ── 固定參數 ──────────────────────────────────────────────────────────────
BASE = dict(
    sl_atr=2.5, tp_atr=10.0,
    min_vol_ratio=1.0,         # 夜盤已在策略內自動放寬至 0.3
    breakeven_trigger_atr=999,
    max_loss_twd=4000.0,
    trend_filter=True, ema200_margin_atr=0.0,
    use_momentum_score=True,
    momentum_rsi_bull=52.0, momentum_rsi_bear=48.0, momentum_session_atr=0.5,
    point_value=10.0,
    scale_out_trigger_atr=0.0, scale_out_qty=1,
    trail_trigger_atr=1.2, trail_dist_atr=1.25,
    max_bars=80, early_cut_bars=40, early_cut_loss_atr=1.5,
    pullback_ema_gap=0.20,
)

# ── 掃描範圍（頻率優先） ──────────────────────────────────────────────────
# v6b baseline: min_adx=23, expand_ratio=1.18, min_di_gap=10, squeeze_ratio=0.90
GRID = {
    'min_adx':           [8, 11, 14, 17, 20],          # vs v6b=23（大幅放寬）
    'expand_ratio':      [1.04, 1.07, 1.10, 1.13],     # vs v6b=1.18
    'min_di_gap':        [3.0, 5.0, 7.0, 10.0],        # vs v6b=10.0
    'squeeze_ratio':     [0.93, 0.97, 1.00],            # 1.00=無需壓縮
    'afternoon_adx_add': [0, 5, 8],                     # afternoon_min_adx = min_adx + add
}

combos = list(product(
    GRID['min_adx'],
    GRID['expand_ratio'],
    GRID['min_di_gap'],
    GRID['squeeze_ratio'],
    GRID['afternoon_adx_add'],
))

print(f'\nTotal combinations: {len(combos)}')
print('Target: ≥ 15 trades/month while keeping WR ≥ 50% and PF ≥ 1.2\n')

# ── 評分函數 ─────────────────────────────────────────────────────────────
def score(r):
    nm = r['n_month']
    if nm < 8:          return -999  # 至少 8 筆/月才考慮
    if r['wr'] < 0.48:  return -999  # WR 下限
    if r['pf'] < 1.15:  return -999  # PF 下限
    # 頻率評分：目標 20/月（ML 過濾後剩 15）
    freq_score = min(nm / 20.0, 1.0)          # 0.4 weight
    wr_score   = (r['wr'] - 0.48) / 0.20      # 0.3 weight（上限 68%→1.0）
    pf_score   = min((r['pf'] - 1.0) / 2.0, 1.0)  # 0.2 weight
    dd_score   = max(0, 1.0 - r['max_dd'] / 20.0)  # 0.1 weight
    return freq_score * 0.4 + wr_score * 0.3 + pf_score * 0.2 + dd_score * 0.1

# ── 執行掃描 ──────────────────────────────────────────────────────────────
results = []
print(f"{'mad':>4} {'er':>5} {'dig':>5} {'sqz':>5} {'add':>3} | "
      f"{'N':>5} {'N/m':>5} {'WR':>6} {'PF':>6} {'MaxDD':>7} {'net':>9}")
print('-' * 72)

done = 0
for mad, er, dig, sqz, add in combos:
    amid = mad + add
    params = {**BASE,
        'min_adx': mad, 'expand_ratio': er, 'min_di_gap': dig,
        'squeeze_ratio': sqz, 'afternoon_min_adx': amid,
    }
    strat = BreakoutTrendStrategy(**params)
    engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    result = engine.run(df_5m, ind, strat, 'tmf_3x')

    trades = result.trades
    n = len(trades)
    done += 1

    if n < 10:
        if done % 50 == 0:
            print(f'  [{done}/{len(combos)}] ...searching...')
        continue

    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses)) if losses else 1
    pf = gp / gl
    wr = len(wins) / n
    net = result.final_balance - 200_000

    eq = result.equity_curve
    peak = eq[0]; max_dd = 0.0
    for v in eq:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd: max_dd = dd

    monthly = defaultdict(int)
    for t in trades:
        monthly[t['entry_time'][:7]] += 1
    n_month = n / n_months

    r = dict(mad=mad, er=er, dig=dig, sqz=sqz, add=add, amid=amid,
             n=n, n_month=n_month, wr=wr, pf=pf, net=net, max_dd=max_dd,
             n_monthly_months=len(monthly))
    s = score(r)
    r['score'] = s
    results.append(r)

    # 只顯示符合頻率目標的候選
    if n_month >= 12 and wr >= 0.48 and pf >= 1.15:
        print(f"{mad:>4} {er:>5.2f} {dig:>5.1f} {sqz:>5.2f} {add:>3d} | "
              f"{n:>5d} {n_month:>5.1f} {wr:>5.1%} {pf:>6.3f} {max_dd:>6.1f}% {net:>+9,.0f}")

print(f'\n掃描完成。共 {len(results)} 組有效結果（N ≥ 10）。')

# ── 結果分析 ─────────────────────────────────────────────────────────────
if not results:
    print('\n[警告] 沒有符合條件的組合！嘗試更放寬的參數。')
else:
    # 1. 頻率排行榜
    freq_results = [r for r in results if r['wr'] >= 0.48 and r['pf'] >= 1.15]
    freq_results.sort(key=lambda x: (-x['n_month'], -x['wr']))

    print(f'\n{"="*72}')
    print(f'TOP 20 — 按 N/月 排序（WR ≥ 48%, PF ≥ 1.15）')
    print(f'{"="*72}')
    print(f"{'mad':>4} {'er':>5} {'dig':>5} {'sqz':>5} {'add':>3} | "
          f"{'N':>5} {'N/m':>5} {'WR':>6} {'PF':>6} {'MaxDD':>7} {'net':>9} {'score':>6}")
    print('-' * 80)
    for r in freq_results[:20]:
        print(f"{r['mad']:>4} {r['er']:>5.2f} {r['dig']:>5.1f} {r['sqz']:>5.2f} {r['add']:>3d} | "
              f"{r['n']:>5d} {r['n_month']:>5.1f} {r['wr']:>5.1%} {r['pf']:>6.3f} "
              f"{r['max_dd']:>6.1f}% {r['net']:>+9,.0f} {r['score']:>6.3f}")

    # 2. 最佳綜合評分排行榜
    scored = [r for r in results if r['score'] > -999]
    scored.sort(key=lambda x: -x['score'])

    print(f'\n{"="*72}')
    print(f'TOP 20 — 按綜合評分排序（頻率×0.4 + WR×0.3 + PF×0.2 + DD×0.1）')
    print(f'{"="*72}')
    print(f"{'mad':>4} {'er':>5} {'dig':>5} {'sqz':>5} {'add':>3} | "
          f"{'N':>5} {'N/m':>5} {'WR':>6} {'PF':>6} {'MaxDD':>7} {'net':>9} {'score':>6}")
    print('-' * 80)
    for r in scored[:20]:
        print(f"{r['mad']:>4} {r['er']:>5.2f} {r['dig']:>5.1f} {r['sqz']:>5.2f} {r['add']:>3d} | "
              f"{r['n']:>5d} {r['n_month']:>5.1f} {r['wr']:>5.1%} {r['pf']:>6.3f} "
              f"{r['max_dd']:>6.1f}% {r['net']:>+9,.0f} {r['score']:>6.3f}")

    # 3. 特別輸出：最接近 15/月的 Top 5
    target_15 = [r for r in results if 13 <= r['n_month'] <= 25 and r['wr'] >= 0.50 and r['pf'] >= 1.3]
    target_15.sort(key=lambda x: (-x['wr'], -x['pf']))

    print(f'\n{"="*72}')
    print(f'目標區間（13-25 筆/月, WR ≥ 50%, PF ≥ 1.3） — 共 {len(target_15)} 組')
    print(f'{"="*72}')
    if target_15:
        print(f"{'mad':>4} {'er':>5} {'dig':>5} {'sqz':>5} {'add':>3} | "
              f"{'N':>5} {'N/m':>5} {'WR':>6} {'PF':>6} {'MaxDD':>7} {'net':>9}")
        print('-' * 72)
        for r in target_15[:10]:
            print(f"{r['mad']:>4} {r['er']:>5.2f} {r['dig']:>5.1f} {r['sqz']:>5.2f} {r['add']:>3d} | "
                  f"{r['n']:>5d} {r['n_month']:>5.1f} {r['wr']:>5.1%} {r['pf']:>6.3f} "
                  f"{r['max_dd']:>6.1f}% {r['net']:>+9,.0f}")
    else:
        print('  (無符合組合，需進一步放寬或嘗試不同策略)')

    # 4. 頻率 vs 品質分佈
    print(f'\n{"="*72}')
    print('頻率分佈（所有有效結果）:')
    bins = [(0,5),(5,10),(10,15),(15,20),(20,30),(30,999)]
    for lo, hi in bins:
        cnt = sum(1 for r in results if lo <= r['n_month'] < hi)
        if cnt > 0:
            subset = [r for r in results if lo <= r['n_month'] < hi]
            avg_wr = sum(r['wr'] for r in subset) / len(subset)
            avg_pf = sum(r['pf'] for r in subset) / len(subset)
            print(f'  {lo:>2}-{hi:<3} /月: {cnt:>4} 組  avg WR={avg_wr:.1%}  avg PF={avg_pf:.2f}')

    # 5. 最終建議
    print(f'\n{"="*72}')
    print('最終建議參數（綜合評分最高，且 N/月 ≥ 13）:')
    print('='*72)
    best = scored[0] if scored else None
    best_freq = target_15[0] if target_15 else None

    if best and best['n_month'] >= 13:
        print(f'  min_adx={best["mad"]}, expand_ratio={best["er"]}, min_di_gap={best["dig"]}, '
              f'squeeze_ratio={best["sqz"]}, afternoon_min_adx={best["amid"]}')
        print(f'  → N/月={best["n_month"]:.1f}, WR={best["wr"]:.1%}, PF={best["pf"]:.3f}, MaxDD={best["max_dd"]:.1f}%')
    elif best_freq:
        print(f'  min_adx={best_freq["mad"]}, expand_ratio={best_freq["er"]}, min_di_gap={best_freq["dig"]}, '
              f'squeeze_ratio={best_freq["sqz"]}, afternoon_min_adx={best_freq["amid"]}')
        print(f'  → N/月={best_freq["n_month"]:.1f}, WR={best_freq["wr"]:.1%}, PF={best_freq["pf"]:.3f}, MaxDD={best_freq["max_dd"]:.1f}%')
    else:
        print('  [警告] 未找到同時滿足頻率和品質要求的組合。')
        print('  考慮: (1) 使用不同策略類型 (2) 降低品質門檻 (3) 多策略組合')
