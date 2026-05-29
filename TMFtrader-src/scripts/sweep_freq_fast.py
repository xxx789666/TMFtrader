"""
頻率優先快速掃描 v2 — 目標 15 筆/月（~5 分鐘完成）
========================================
已知：min_adx=14, expand_ratio=1.07, min_di_gap=5.0, squeeze=0.97 → 17.5 筆/月
此腳本做精準搜尋：找到最佳 WR/PF 的「15-25 筆/月」組合

執行：
    cd TMFtrader-src
    python scripts/sweep_freq_fast.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
import time
from itertools import product
from collections import defaultdict
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy

# ── 載入全時段資料 ────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
CSV  = ROOT / 'data' / 'historical' / 'tmf_20260411_full_1m.csv'

print('Loading data (all sessions)...')
df_1m = pd.read_csv(CSV, parse_dates=['datetime']).sort_values('datetime').reset_index(drop=True)
df    = df_1m.set_index('datetime')
df_5m = df.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna().reset_index()
n_months = (df_5m['datetime'].iloc[-1] - df_5m['datetime'].iloc[0]).days / 30.44
print(f'  Bars={len(df_5m):,}  Period={n_months:.1f}m  {df_5m["datetime"].iloc[0]:%Y-%m-%d} ~ {df_5m["datetime"].iloc[-1]:%Y-%m-%d}')

print('Precomputing indicators...')
t0 = time.time()
ind = precompute_all(df_5m, verbose=False)
print(f'  Done in {time.time()-t0:.1f}s')

# ── 固定出場參數（v6b 最佳）─────────────────────────────────────────────
BASE = dict(
    sl_atr=2.5, tp_atr=10.0,
    min_vol_ratio=1.0,
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
    squeeze_grace_bars=1,
)

# ── 精簡掃描格（~72 組合，~6 分鐘）──────────────────────────────────────
# 重點：(1) 找 15-25 筆/月  (2) 最大化 WR 和 PF
GRID = {
    'min_adx':           [10, 13, 16, 19],      # 核心：ADX 門檻
    'expand_ratio':      [1.04, 1.07, 1.10],    # ATR 擴張比
    'min_di_gap':        [3.0, 5.0, 7.0],       # DI 方向差
    'squeeze_ratio':     [0.95, 1.00],           # 0.95=需壓縮 / 1.00=無需壓縮
    # afternoon_min_adx = min_adx + add (0=不加嚴 / 5=略微加嚴)
    'afternoon_add':     [0, 5],
}

combos = list(product(
    GRID['min_adx'], GRID['expand_ratio'], GRID['min_di_gap'],
    GRID['squeeze_ratio'], GRID['afternoon_add'],
))
print(f'\nGrid size: {len(combos)} combos  (est {len(combos)*5.2/60:.0f} min)')
print('='*72)

# ── 執行 ─────────────────────────────────────────────────────────────────
results = []
t_total = time.time()

for i, (mad, er, dig, sqz, add) in enumerate(combos, 1):
    amid = mad + add
    params = {**BASE,
        'min_adx': mad, 'expand_ratio': er, 'min_di_gap': dig,
        'squeeze_ratio': sqz, 'afternoon_min_adx': amid,
    }
    strat  = BreakoutTrendStrategy(**params)
    engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    result = engine.run(df_5m, ind, strat, 'tmf_3x')

    trades = result.trades
    n = len(trades)
    if n < 10:
        continue

    wins   = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses)) if losses else 1e-9
    pf = gp / gl
    wr = len(wins) / n
    net = result.final_balance - 200_000
    nm = n / n_months  # trades per month

    eq = result.equity_curve
    peak = eq[0]; max_dd = 0.0
    for v in eq:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd: max_dd = dd

    monthly = defaultdict(int)
    for t in trades:
        monthly[t['entry_time'][:7]] += 1
    green_months = sum(1 for t in trades if t['pnl'] > 0) / max(len(monthly), 1)  # dummy

    results.append(dict(mad=mad, er=er, dig=dig, sqz=sqz, add=add, amid=amid,
                        n=n, nm=nm, wr=wr, pf=pf, net=net, max_dd=max_dd,
                        n_months_active=len(monthly)))

    if i % 24 == 0 or i == len(combos):
        elapsed = time.time() - t_total
        eta = elapsed / i * (len(combos) - i)
        print(f'  Progress: {i}/{len(combos)}  elapsed={elapsed:.0f}s  ETA={eta:.0f}s')

print(f'\nDone. {len(results)} valid results in {time.time()-t_total:.0f}s')
print()

# ── 分析 ─────────────────────────────────────────────────────────────────

def score(r):
    nm = r['nm']
    if nm < 10 or r['wr'] < 0.48 or r['pf'] < 1.15:
        return -999
    freq_s = min(nm / 20.0, 1.0)                      # 目標 20/月 → 1.0
    wr_s   = min((r['wr'] - 0.48) / 0.20, 1.0)
    pf_s   = min((r['pf'] - 1.0) / 2.5, 1.0)
    dd_s   = max(0.0, 1.0 - r['max_dd'] / 25.0)
    return freq_s * 0.40 + wr_s * 0.30 + pf_s * 0.20 + dd_s * 0.10

HDR = f"{'mad':>4} {'er':>5} {'dig':>5} {'sqz':>5} {'add':>3} | {'N':>5} {'N/m':>5} {'WR':>6} {'PF':>6} {'MaxDD':>7} {'net':>9}"
SEP = '-' * 76

# 1. 頻率排行（N/月 15-25 優先區）
print('='*76)
print('▶ 目標區間（13-25 筆/月）中 WR 最高者')
print('='*76)
target = [r for r in results if 13 <= r['nm'] <= 25 and r['wr'] >= 0.50 and r['pf'] >= 1.2]
target.sort(key=lambda x: (-x['wr'], -x['pf']))
print(HDR)
print(SEP)
if target:
    for r in target[:15]:
        print(f"{r['mad']:>4} {r['er']:>5.2f} {r['dig']:>5.1f} {r['sqz']:>5.2f} {r['add']:>3d} | "
              f"{r['n']:>5d} {r['nm']:>5.1f} {r['wr']:>5.1%} {r['pf']:>6.3f} "
              f"{r['max_dd']:>6.1f}% {r['net']:>+9,.0f}")
else:
    print('  (無符合組合)')

# 2. 綜合評分排行
print()
print('='*76)
print('▶ 綜合評分 Top 20（頻率0.4+WR0.3+PF0.2+DD0.1）')
print('='*76)
scored = sorted([r for r in results if score(r) > -999], key=lambda x: -score(x))
print(HDR)
print(SEP)
for r in scored[:20]:
    s = score(r)
    print(f"{r['mad']:>4} {r['er']:>5.2f} {r['dig']:>5.1f} {r['sqz']:>5.2f} {r['add']:>3d} | "
          f"{r['n']:>5d} {r['nm']:>5.1f} {r['wr']:>5.1%} {r['pf']:>6.3f} "
          f"{r['max_dd']:>6.1f}% {r['net']:>+9,.0f}  s={s:.3f}")

# 3. 頻率 vs 品質分佈
print()
print('='*76)
print('▶ 頻率分佈統計')
print('='*76)
for lo, hi in [(0,5),(5,10),(10,15),(15,20),(20,30),(30,99)]:
    sub = [r for r in results if lo <= r['nm'] < hi]
    if sub:
        avg_wr = sum(r['wr'] for r in sub) / len(sub)
        avg_pf = sum(r['pf'] for r in sub) / len(sub)
        print(f'  {lo:>3}-{hi:<3} /月: {len(sub):>4} 組  avgWR={avg_wr:.1%}  avgPF={avg_pf:.2f}')

# 4. 最終建議
print()
print('='*76)
print('▶ 最終建議（Layer 1 v7 候選）')
print('='*76)
best = scored[0] if scored else (target[0] if target else None)
if best:
    print(f'''  參數:
    min_adx          = {best["mad"]}
    expand_ratio     = {best["er"]}
    min_di_gap       = {best["dig"]}
    squeeze_ratio    = {best["sqz"]}
    afternoon_min_adx= {best["amid"]}

  預期 Layer 1 (v7):
    N/月  = {best["nm"]:.1f}
    WR    = {best["wr"]:.1%}
    PF    = {best["pf"]:.3f}
    MaxDD = {best["max_dd"]:.1f}%
    Net   = {best["net"]:+,.0f} TWD ({n_months:.0f}m)

  下一步:
    → 以上述參數重新產生訓練標籤（1-7 Stages 重跑）
    → ML 過濾後預期：WR ↑ ~5pp, N/月 ~{best["nm"]*0.75:.0f}-{best["nm"]*0.85:.0f}
''')
else:
    print('  [未找到] 請降低品質門檻或嘗試其他策略')
