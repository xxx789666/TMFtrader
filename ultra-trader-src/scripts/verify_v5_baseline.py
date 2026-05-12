"""
驗證修正後的 squeeze 邏輯是否能重現 v5 基線
- squeeze_grace_bars=1 應等同原始行為
- 目標：n≈96, PF≈2.48, WR≈60.4%

使用方式：
    cd ultra-trader-src
    python scripts/verify_v5_baseline.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
from collections import defaultdict
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy

# v5 最終參數（與 gen_backtest_report_v5.py 完全一致）
PARAMS = dict(
    sl_atr=2.5, tp_atr=10.0,
    trail_trigger_atr=1.2,
    trail_dist_atr=1.25,
    max_bars=80,
    min_adx=20.0, afternoon_min_adx=32.0,
    min_di_gap=10.0,
    squeeze_ratio=0.90,
    expand_ratio=1.18,
    min_vol_ratio=1.0,
    pullback_ema_gap=0.20,
    breakeven_trigger_atr=999,
    early_cut_bars=30,
    early_cut_loss_atr=1.5,
    max_loss_twd=4000.0,
    trend_filter=True, ema200_margin_atr=0.0,
    use_momentum_score=True,
    momentum_rsi_bull=52.0, momentum_rsi_bear=46.0,
    momentum_session_atr=0.5,
    point_value=10.0,
    scale_out_trigger_atr=0.0, scale_out_qty=1,
)

ROOT = Path(__file__).parent.parent

# 優先使用 CSV（原始 v5 資料），不存在才 fallback 到 parquet
CSV_PATH = ROOT / 'data' / 'tmf_20260411_full_1m.csv'
PARQUET_PATH = ROOT / 'data' / 'historical' / 'tmf_5y_1m.parquet'
CSV_PATH2 = ROOT / 'data' / 'historical' / 'tmf_20260411_full_1m.csv'

print('=' * 60)
print('V5 基線驗證：squeeze_grace_bars=1')
print('=' * 60)

print('\n[1/3] 載入資料...')
csv = CSV_PATH if CSV_PATH.exists() else (CSV_PATH2 if CSV_PATH2.exists() else None)
if csv:
    df_1m = pd.read_csv(csv, parse_dates=['datetime']).sort_values('datetime').reset_index(drop=True)
    print(f'  來源：CSV  ({len(df_1m):,} bars)')
else:
    df_1m = pd.read_parquet(PARQUET_PATH)
    if 'datetime' not in df_1m.columns:
        df_1m = df_1m.reset_index()
    df_1m = df_1m.sort_values('datetime').reset_index(drop=True)
    print(f'  來源：Parquet  ({len(df_1m):,} bars)')

df = df_1m.set_index('datetime')
df_5m = df.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
df_5m = df_5m.between_time('08:45', '13:30').reset_index()
print(f'  5min 日盤: {len(df_5m):,} bars  {df_5m["datetime"].iloc[0]} ~ {df_5m["datetime"].iloc[-1]}')

print('\n[2/3] 預計算指標...')
ind = precompute_all(df_5m, verbose=False)

print('\n[3/3] 回測（grace_bars=1）...')

def run_and_print(grace_bars, label=None):
    params = dict(PARAMS, squeeze_grace_bars=grace_bars)
    strat  = BreakoutTrendStrategy(**params)
    engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    result = engine.run(df_5m, ind, strat, 'tmf_3x')
    trades = result.trades
    n = len(trades)
    if n == 0:
        print(f'  {label or f"grace={grace_bars}"}: 0 筆交易')
        return

    wins   = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses)) if losses else 1
    pf = gp / gl
    wr = len(wins) / n * 100
    net = result.final_balance - 200_000

    monthly = defaultdict(float)
    for t in trades:
        monthly[t['entry_time'][:7]] += t['pnl']
    n_green = sum(1 for v in monthly.values() if v > 0)
    b = 200_000.0; n6 = 0
    for ym in sorted(monthly):
        if monthly[ym] / b >= 0.06: n6 += 1
        b += monthly[ym]

    eq = result.equity_curve
    max_dd = 0.0
    peak = eq[0]
    for v in eq:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd: max_dd = dd

    tag = label or f'grace={grace_bars}'
    print(f'\n  [{tag}]')
    print(f'  {n}筆  WR={wr:.1f}%  PF={pf:.3f}  淨利={net:+,.0f}  MaxDD={max_dd:.1f}%')
    print(f'  綠月={n_green}/{len(monthly)}  達標(≥6%)={n6}個')

    # v5 基線對比
    print(f'\n  --- 對比 v5 基線 ---')
    print(f'  筆數  : {n:3d}  (v5=96)  {"✅" if abs(n-96)<=5 else "⚠️"}')
    print(f'  WR    : {wr:.1f}%  (v5=60.4%)  {"✅" if abs(wr-60.4)<=3 else "⚠️"}')
    print(f'  PF    : {pf:.3f}  (v5=2.480)  {"✅" if abs(pf-2.48)<=0.15 else "⚠️"}')
    print(f'  MaxDD : {max_dd:.1f}%  (v5=6.4%)  {"✅" if max_dd<=8.0 else "⚠️"}')
    print(f'  淨利  : {net:+,.0f}  (v5=+203,430)  {"✅" if abs(net-203430)<=15000 else "⚠️"}')

run_and_print(1, 'grace_bars=1（等同原始）')

print('\n\n為對比，也跑 grace_bars=2 和 grace_bars=3：')
for g in [2, 3]:
    run_and_print(g)

print('\n' + '=' * 60)
print('完成')
