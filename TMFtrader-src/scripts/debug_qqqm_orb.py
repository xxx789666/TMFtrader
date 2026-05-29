"""
Quick debug: verify QQQM ORB direction and exit reasons after fix.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.orb import ORBStrategy
from collections import Counter

ROOT = Path(__file__).parent.parent

print('Loading QQQM data...')
QQQM = ROOT / 'data' / 'historical' / 'ml' / 'QQQM_5m.parquet'
df = pd.read_parquet(QQQM).reset_index()
if 'datetime' not in df.columns:
    df = df.rename(columns={df.columns[0]: 'datetime'})
df['datetime'] = pd.to_datetime(df['datetime'])
df = df.sort_values('datetime').reset_index(drop=True)
n_months = (df['datetime'].iloc[-1] - df['datetime'].iloc[0]).days / 30.44
print(f'  {len(df):,} bars, {n_months:.1f} months')
print(f'  Time range sample: {df["datetime"].iloc[0]} ~ {df["datetime"].iloc[-1]}')

# Show sample timestamps to confirm UTC+2
print(f'  First 3 bars: {df["datetime"].iloc[:3].tolist()}')

print('Precomputing indicators...')
ind = precompute_all(df, verbose=False)
print('  Done.')

# Test with trend_filter=True, session_start=16:30
params = dict(
    point_value=10.0, max_loss_twd=0.0,
    tp_atr=10.0, early_cut_loss_atr=1.5,
    allow_both_directions=True, trend_filter=True,
    session_start=(16, 30), force_close_time=(20, 55),
    orb_minutes=30, entry_filter_atr=0.0,
    sl_type='atr', sl_atr=2.0, sl_range_buffer=0.3,
    trail_trigger_atr=0.8, trail_dist_atr=1.25,
    adx_min=0, max_bars=60, early_cut_bars=30,
)

strat  = ORBStrategy(**params)
engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
result = engine.run(df, ind, strat, 'tmf_3x')
trades = result.trades

print(f'\nTotal trades: {len(trades)}')
if not trades:
    print('  [NO TRADES] — check session_start / data timestamps')
else:
    longs  = [t for t in trades if t['side'] == 'long']
    shorts = [t for t in trades if t['side'] == 'short']
    print(f'  Long: {len(longs)}  Short: {len(shorts)}')

    # Show available keys
    print(f'\nTrade keys: {list(trades[0].keys())}')

    reason_key = 'exit_reason' if 'exit_reason' in trades[0] else 'reason'
    exits = Counter(t.get(reason_key, t.get('exit_reason', t.get('reason', '?'))) for t in trades)
    print(f'\nExit reasons:')
    for k, v in exits.most_common(10):
        print(f'  {v:>5}x  {k}')

    # Direction WR
    ep_key  = 'entry_price'  if 'entry_price'  in trades[0] else 'entry'
    ex_key  = 'exit_price'   if 'exit_price'   in trades[0] else 'exit'
    wins = sum(1 for t in trades if (
        (t['side']=='long'  and t[ex_key] > t[ep_key]) or
        (t['side']=='short' and t[ex_key] < t[ep_key])
    ))
    wr = wins / len(trades) if trades else 0
    nm = len(trades) / n_months
    print(f'\nDirection WR: {wr:.1%}  ({wins}/{len(trades)})')
    print(f'N/month: {nm:.1f}')

    # Sample trades
    print('\nFirst 10 trades:')
    for t in trades[:10]:
        ep = t[ep_key]; ex = t[ex_key]
        dir_ok = (t['side']=='long' and ex > ep) or (t['side']=='short' and ex < ep)
        r = t.get(reason_key, '?')
        print(f"  {t['entry_time'][:16]} {t['side']:>5} @{ep:.2f} → {ex:.2f}  "
              f"{'WIN' if dir_ok else 'LOSE'}  [{str(r)[:30]}]")
