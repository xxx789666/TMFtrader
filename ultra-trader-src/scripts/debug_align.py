"""
Quick test: MXF ORB signal direction vs QQQM daily direction alignment
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
import datetime as dt
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.orb import ORBStrategy
from collections import Counter

ROOT = Path(__file__).parent.parent

# Load MXF night
print('Loading MXF night session...')
CSV = ROOT / 'data' / 'historical' / 'tmf_20260411_full_1m.csv'
df_1m = pd.read_csv(CSV, parse_dates=['datetime']).sort_values('datetime').reset_index(drop=True)
df_raw = df_1m.set_index('datetime').resample('5min').agg(
    {'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
df_mxf = df_raw.between_time('21:30', '04:00').reset_index()
n_m = (df_mxf['datetime'].iloc[-1] - df_mxf['datetime'].iloc[0]).days / 30.44
print(f'  MXF night: {len(df_mxf):,} bars, {n_m:.1f} months')

# Load QQQM
print('Loading QQQM...')
QQQM = ROOT / 'data' / 'historical' / 'ml' / 'QQQM_5m.parquet'
df_q = pd.read_parquet(QQQM).reset_index()
if 'datetime' not in df_q.columns:
    df_q = df_q.rename(columns={df_q.columns[0]: 'datetime'})
df_q['datetime'] = pd.to_datetime(df_q['datetime'])
df_q = df_q.sort_values('datetime').reset_index(drop=True)

# Build QQQM daily direction
df_q['date'] = df_q['datetime'].dt.date
daily_open  = df_q.groupby('date')['open'].first()
daily_close = df_q.groupby('date')['close'].last()
qqqm_dir = {}
for d in daily_open.index:
    qqqm_dir[d] = 'up' if daily_close[d] >= daily_open[d] else 'down'
print(f'  QQQM daily dates: {len(qqqm_dir)} days')
# Show a few sample days
sample_days = sorted(qqqm_dir.keys())[:5]
for d in sample_days:
    print(f'    {d}: {qqqm_dir[d]}  open={daily_open[d]:.2f} close={daily_close[d]:.2f}')

print('Precomputing MXF indicators...')
ind_mxf = precompute_all(df_mxf, verbose=False)

# Run MXF ORB backtest
params = dict(
    point_value=10.0, max_loss_twd=4000.0,
    tp_atr=10.0, early_cut_loss_atr=1.5,
    allow_both_directions=True, trend_filter=False,
    session_start=(21, 30), force_close_time=(4, 0),
    orb_minutes=45, entry_filter_atr=0.0,
    sl_type='atr', sl_atr=2.0, sl_range_buffer=0.3,
    trail_trigger_atr=0.8, trail_dist_atr=1.25,
    adx_min=0, max_bars=60, early_cut_bars=30,
)

strat  = ORBStrategy(**params)
engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
result = engine.run(df_mxf, ind_mxf, strat, 'tmf_3x')
trades = result.trades
print(f'\nMXF trades: {len(trades)}')

# Direction alignment
aligned = 0; total = 0; no_match = 0
dir_counts = Counter()
for t in trades:
    entry_dt = pd.Timestamp(t['entry_time'])
    etime = entry_dt.time()
    if etime >= dt.time(21, 30):
        us_date = entry_dt.date()
    else:
        us_date = (entry_dt - pd.Timedelta(days=1)).date()

    mxf_dir = 'up' if t['side'] == 'long' else 'down'
    dir_counts[mxf_dir] += 1

    if us_date in qqqm_dir:
        qd = qqqm_dir[us_date]
        if qd == mxf_dir:
            aligned += 1
        total += 1
    else:
        no_match += 1

print(f'MXF direction: {dict(dir_counts)}')
print(f'QQQM matched: {total} ({no_match} dates not found)')
if total > 0:
    print(f'Direction alignment: {aligned}/{total} = {aligned/total:.1%}')
    print(f'(random baseline ≈ 50%)')

# MXF stats
wins = [t for t in trades if t['pnl'] > 0]
losses = [t for t in trades if t['pnl'] <= 0]
if trades:
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses)) or 1e-9
    print(f'\nMXF WR={len(wins)/len(trades):.1%}  PF={gp/gl:.3f}  N/m={len(trades)/n_m:.1f}')

# Sample first 5 trades with alignment
print('\nSample trades (first 5 with QQQM match):')
shown = 0
for t in trades:
    entry_dt = pd.Timestamp(t['entry_time'])
    etime = entry_dt.time()
    if etime >= dt.time(21, 30):
        us_date = entry_dt.date()
    else:
        us_date = (entry_dt - pd.Timedelta(days=1)).date()
    if us_date in qqqm_dir and shown < 5:
        qd = qqqm_dir[us_date]
        mxf_dir = 'up' if t['side'] == 'long' else 'down'
        ok = '✓' if qd == mxf_dir else '✗'
        print(f"  {t['entry_time'][:16]} MXF={mxf_dir:<4} QQQM={qd:<4} {ok}")
        shown += 1
