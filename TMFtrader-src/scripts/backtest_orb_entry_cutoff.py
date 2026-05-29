"""
回測：ORB 最晚進場時間限制
比較 max_entry_time = [無限制, 01:00, 01:30, 02:00] 的績效差異
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

ROOT = Path(__file__).parent.parent

# ── 載入資料
print('Loading MXF night session data...')
CSV = ROOT / 'data' / 'historical' / 'tmf_20260411_full_1m.csv'
df_1m = pd.read_csv(CSV, parse_dates=['datetime']).sort_values('datetime').reset_index(drop=True)
df_raw = df_1m.set_index('datetime').resample('5min').agg(
    {'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
df = df_raw.between_time('21:30', '04:00').reset_index()
n_months = (df['datetime'].iloc[-1] - df['datetime'].iloc[0]).days / 30.44
print(f'  {len(df):,} bars, {n_months:.1f} months')

print('Precomputing indicators...')
ind = precompute_all(df, verbose=False)
print('  Done.\n')

# ── 當前上線參數（Phase 5）
BASE = dict(
    orb_minutes=45,
    entry_filter_atr=0.0,
    adx_min=0,
    sl_type='atr',
    sl_atr=2.0,
    trail_trigger_atr=0.8,
    trail_dist_atr=0.3,
    max_bars=60,
    early_cut_bars=30,
    early_cut_loss_atr=1.5,
    tp_atr=10.0,
    max_loss_twd=4000.0,
    allow_both_directions=True,
    trend_filter=False,
    session_start=(21, 30),
    force_close_time=(4, 0),
    point_value=10.0,
)

# ── 測試不同 max_entry_time
configs = [
    ("無限制（現行）", (0, 0)),
    ("max_entry 00:30", (0, 30)),
    ("max_entry 01:00", (1, 0)),
    ("max_entry 01:30", (1, 30)),
    ("max_entry 02:00", (2, 0)),
]

print(f"{'配置':<20} {'交易數':>6} {'月均':>5} {'勝率':>6} {'PF':>6} {'淨利':>10} {'最大DD%':>8} {'平均持倉根':>10}")
print("-" * 85)

for label, met in configs:
    params = {**BASE, 'max_entry_time': met}
    strat = ORBStrategy(**params)
    engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    result = engine.run(df, ind, strat, 'tmf_3x')
    trades = result.trades
    n = len(trades)
    if n == 0:
        print(f"{label:<20} {'N/A':>6}")
        continue

    wins = [t for t in trades if t['pnl'] > 0]
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0)) or 1e-9
    pf = gp / gl
    wr = len(wins) / n
    net = result.final_balance - 200_000
    eq = result.equity_curve
    peak = eq[0]; max_dd = 0.0
    for v in eq:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd: max_dd = dd
    nm = n / n_months
    avg_bars = sum(t.get('bars_held', 0) for t in trades) / n

    # 統計 session close 出場數
    sc_count = sum(1 for t in trades if 'session close' in t.get('reason', '').lower() or 'session' in t.get('reason', '').lower())

    print(f"{label:<20} {n:>6} {nm:>5.1f} {wr:>6.1%} {pf:>6.2f} {net:>10,.0f} {max_dd:>8.2f} {avg_bars:>10.1f}  SC={sc_count}")

print("\nSC = session close 被動平倉次數")
